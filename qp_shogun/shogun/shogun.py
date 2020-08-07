# -----------------------------------------------------------------------------
# Copyright (c) 2014--, The Qiita Development Team.
#
# Distributed under the terms of the BSD 3-clause License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------
from os.path import join
from .utils import readfq, import_shogun_biom
from qp_shogun.utils import (make_read_pairs_per_sample, _run_commands)
import gzip
from qiita_client import ArtifactInfo
from qiita_client.util import system_call
from biom import util

SHOGUN_PARAMS = {
    'Database': 'database', 'Aligner tool': 'aligner',
    'Number of threads': 'threads', 'Capitalist': 'capitalist',
    'Percent identity': 'percent_id'}

ALN2EXT = {
    # if you uncomment these lines, also uncomment the tests:
    #    test_shogun_utree, test_shogun_burst
    # 'utree': 'tsv',
    # 'burst': 'b6',
    'bowtie2': 'sam'
}


def generate_fna_file(temp_path, samples):
    # Combines reverse and forward seqs per sample
    # Returns filepaths of new combined files
    output_fp = join(temp_path, 'combined.fna')
    output = open(output_fp, "a")
    count = 0
    for run_prefix, sample, f_fp, r_fp in samples:
        with gzip.open(f_fp, 'rt') as fp:
            # Loop through forward file
            for header, seq, qual in readfq(fp):
                output.write(">%s_%d\n" % (sample, count))
                output.write("%s\n" % seq)
                count += 1
        if r_fp is not None:
            with gzip.open(r_fp, 'rt') as fp:
                # Loop through reverse file
                for header, seq, qual in readfq(fp):
                    output.write(">%s_%d\n" % (sample, count))
                    output.write("%s\n" % seq)
                    count += 1
    output.close()

    return output_fp


def _format_params(parameters, func_params):
    params = {}
    # Loop through all of the commands alphabetically
    for param in func_params:
        # Find the value using long parameter names
        parameter = func_params[param]
        value = parameters[param]
        params[parameter] = value

    return params


def generate_shogun_align_commands(input_fp, out_dir, parameters):
    cmds = []
    cmds.append(
        'shogun align --aligner {aligner} --threads {threads} '
        '--database {database} --input {input} --output {output} '
        '--percent_id {percent_id}'.format(
            aligner=parameters['aligner'],
            threads=parameters['threads'],
            database=parameters['database'],
            percent_id=parameters['percent_id'],
            input=input_fp,
            output=out_dir))

    return cmds


def generate_shogun_assign_taxonomy_commands(out_dir, parameters):
    cmds = []
    ext = ALN2EXT[parameters['aligner']]
    output_fp = join(out_dir, 'profile.tsv')
    capitalist = ('--capitalist' if parameters['capitalist']
                  else '--no-capitalist')
    cmds.append(
        'shogun assign_taxonomy '
        '--aligner {aligner} '
        '{capitalist} '
        '--database {database} '
        '--input {input} --output {output}'.format(
            aligner=parameters['aligner'],
            database=parameters['database'],
            capitalist=capitalist,
            input=join(out_dir, 'alignment.%s.%s' % (
                parameters['aligner'], ext)),
            output=output_fp))

    return cmds, output_fp


def generate_shogun_functional_commands(profile_dir, out_dir,
                                        parameters, sel_level):
    cmds = []
    output = join(out_dir, 'functional')
    cmds.append(
        'shogun functional '
        '--database {database} '
        '--input {input} '
        '--output {output} '
        '--level {level}'.format(
            database=parameters['database'],
            input=profile_dir,
            output=output,
            level=sel_level))

    return cmds, output


def generate_shogun_redist_commands(profile_dir, out_dir,
                                    parameters, sel_level):
    cmds = []
    output = join(out_dir, 'profile.redist.%s.tsv' % sel_level)
    cmds.append(
        'shogun redistribute '
        '--database {database} '
        '--level {level} '
        '--input {input} '
        '--output {output}'.format(
            database=parameters['database'],
            input=profile_dir,
            output=output,
            level=sel_level))

    return cmds, output


def run_shogun_to_biom(in_fp, biom_in, out_dir, level, version='alignment'):
    if version in ('redist', 'alignment'):
        output_fp = join(out_dir, 'otu_table.%s.%s.biom'
                         % (version, level))
    else:
        output_fp = join(out_dir, 'otu_table.%s.%s.%s.biom'
                         % (version, level, biom_in[0]))
    tb = import_shogun_biom(in_fp, biom_in[1],
                            biom_in[2], biom_in[3])
    with util.biom_open(output_fp, 'w') as f:
        tb.to_hdf5(f, "shogun")

    return output_fp


def shogun(qclient, job_id, parameters, out_dir):
    """Run Shogun with the given parameters

    Parameters
    ----------
    qclient : tgp.qiita_client.QiitaClient
        The Qiita server client
    job_id : str
        The job id
    parameters : dict
        The parameter values to run split libraries
    out_dir : str
        The path to the job's output directory

    Returns
    -------
    bool, list, str
        The results of the job
    """
    # Step 1 get the rest of the information need to run Atropos
    qclient.update_job_step(job_id, "Step 1 of 7: Collecting information")
    artifact_id = parameters['input']
    del parameters['input']

    # Get the artifact filepath information
    artifact_info = qclient.get("/qiita_db/artifacts/%s/" % artifact_id)
    fps = artifact_info['files']

    # Get the artifact metadata
    prep_info = qclient.get('/qiita_db/prep_template/%s/'
                            % artifact_info['prep_information'][0])
    qiime_map = prep_info['qiime-map']

    # Step 2 converting to fna
    qclient.update_job_step(
        job_id, "Step 2 of 7: Converting to FNA for Shogun")

    rs = fps['raw_reverse_seqs'] if 'raw_reverse_seqs' in fps else []
    samples = make_read_pairs_per_sample(
        fps['raw_forward_seqs'], rs, qiime_map)

    # Combining files
    comb_fp = generate_fna_file(out_dir, samples)

    # Formatting parameters
    parameters = _format_params(parameters, SHOGUN_PARAMS)

    # Step 3 align
    align_cmd = generate_shogun_align_commands(
        comb_fp, out_dir, parameters)
    sys_msg = "Step 3 of 7: Aligning FNA with Shogun (%d/{0})".format(
        len(align_cmd))
    success, msg = _run_commands(
        qclient, job_id, align_cmd, sys_msg, 'Shogun Align')

    if not success:
        return False, None, msg

    # Step 4 taxonomic profile
    sys_msg = "Step 4 of 7: Taxonomic profile with Shogun (%d/{0})"
    assign_cmd, profile_fp = generate_shogun_assign_taxonomy_commands(
        out_dir, parameters)
    success, msg = _run_commands(
        qclient, job_id, assign_cmd, sys_msg, 'Shogun taxonomy assignment')
    if not success:
        return False, None, msg

    sys_msg = "Step 5 of 7: Compressing and converting alignment to BIOM"
    qclient.update_job_step(job_id, sys_msg)
    alignment_fp = join(out_dir, 'alignment.%s.%s' % (
        parameters['aligner'], ALN2EXT[parameters['aligner']]))
    xz_cmd = 'xz -9 -T%s %s' % (parameters['threads'], alignment_fp)
    std_out, std_err, return_value = system_call(xz_cmd)
    if return_value != 0:
        error_msg = ("Error during %s:\nStd out: %s\nStd err: %s"
                     "\n\nCommand run was:\n%s"
                     % (sys_msg, std_out, std_err, xz_cmd))
        return False, None, error_msg
    output = run_shogun_to_biom(profile_fp, [None, None, None, True],
                                out_dir, 'profile')

    alignment_fp_xz = '%s.xz' % alignment_fp
    ainfo = [ArtifactInfo('Shogun Alignment Profile', 'BIOM',
                          [(output, 'biom'),
                           (alignment_fp_xz, 'log')])]

    # Step 5 redistribute profile
    sys_msg = "Step 6 of 7: Redistributed profile with Shogun (%d/{0})"
    levels = ['phylum', 'genus', 'species']
    redist_fps = []
    for level in levels:
        redist_cmd, output = generate_shogun_redist_commands(
            profile_fp, out_dir, parameters, level)
        redist_fps.append(output)
        success, msg = _run_commands(
            qclient, job_id, redist_cmd, sys_msg, 'Shogun redistribute')
        if not success:
            return False, None, msg
    # Converting redistributed files to biom
    for redist_fp, level in zip(redist_fps, levels):
        biom_in = ["redist", None, '', True]
        output = run_shogun_to_biom(
            redist_fp, biom_in, out_dir, level, 'redist')
        aname = 'Taxonomic Predictions - %s' % level
        ainfo.append(ArtifactInfo(aname, 'BIOM', [(output, 'biom')]))

    # Woltka only works with WOL databases
    if 'wol' in parameters['database']:
        sys_msg = "Step 7 of 7: Wolka gOTU and per-gene tables (%d/{0})"
        per_genome_fp = join(out_dir, 'woltka_per_genome.biom')
        per_gene_fp = join(out_dir, 'woltka_per_gene.biom')
        coord_fp = join(parameters['database'], 'WoLr1.coords')
        commands = [
            'woltka classify -i %s -o %s' % (alignment_fp_xz, per_genome_fp),
            'woltka classify -i %s -c %s -o %s' % (
                alignment_fp_xz, coord_fp, per_gene_fp)]

        success, msg = _run_commands(
            qclient, job_id, commands, sys_msg, 'Woltka')
        if not success:
            return False, None, msg

        ainfo.extend([
            ArtifactInfo('Woltka - per genome', 'BIOM', [
                (per_genome_fp, 'biom')]),
            ArtifactInfo('Woltka - per gene', 'BIOM', [
                (per_gene_fp, 'biom')])])

    return True, ainfo, ""

#
# This code is not currently needed but it will be used for analysis, so
# leaving here to avoid having to rewrite it
#

# # Step 6 functional profile
# sys_msg = "Step 6 of 7: Functional profile with Shogun (%d/{0})"
# levels = ['species']
# func_fp = ''
# for level in levels:
#     func_cmd, output = generate_shogun_functional_commands(
#         profile_fp, out_dir, parameters, level)
#     func_fp = output
#     success, msg = _run_commands(
#         qclient, job_id, func_cmd, sys_msg, 'Shogun functional')
#     if not success:
#         return False, None, msg
# # Coverting funcitonal files to biom
# func_db_fp = shogun_db_functional_parser(parameters['database'])
# for level in levels:
#     func_to_biom_fps = [
#         ["kegg.modules.coverage", func_db_fp['module'],
#          'module', False],
#         ["kegg.modules", func_db_fp['module'], 'module', False],
#         ["kegg.pathways.coverage", func_db_fp['pathway'],
#          'pathway', False],
#         ["kegg.pathways", func_db_fp['pathway'], 'pathway', False],
#         ["kegg", func_db_fp['enzyme'], 'enzyme', True],
#         ["normalized", func_db_fp['enzyme'], 'pathway', True]]
#
#     for biom_in in func_to_biom_fps:
#         biom_in_fp = join(func_fp, "profile.%s.%s.txt"
#                           % (level, biom_in[0]))
#         output = run_shogun_to_biom(biom_in_fp, biom_in, out_dir,
#                                     level, 'func')
#         if biom_in[0] == 'kegg.modules.coverage':
#             atype = 'KEGG Modules Coverage'
#         elif biom_in[0] == 'kegg.modules':
#             atype = 'KEGG Modules'
#         elif biom_in[0] == 'kegg.pathways.coverage':
#             atype = 'KEGG Pathways Coverage'
#         elif biom_in[0] == 'kegg.pathways':
#             atype = 'KEGG Pathways'
#         elif biom_in[0] == 'kegg':
#             atype = 'KEGG'
#         elif biom_in[0] == 'normalized':
#             atype = 'Normalized'
#         else:
#             # this should never happen but adding for completeness
#             return False, None, "Not a valid format: %s" % biom_in[0]
#         aname = 'Functional Predictions - %s, %s' % (level, atype)
#         ainfo.append(ArtifactInfo(aname, 'BIOM', [(output, 'biom')]))
