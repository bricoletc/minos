import logging
import os

import pyfastaq
import pysam
import pandas as pd

from Bio import pairwise2, SeqIO

from cluster_vcf_records import vcf_clusterer, vcf_file_read

from minos import dependencies, dnadiff, plots, utils

class Error (Exception): pass

class DnadiffMappingBasedVerifier:
    '''dnadiff_snps_file = file of snp calls generated by dnadiff.
    dnadiff_file1 = file containing reference sequence passed to dnadiff.
    dnadiff_file2 = file containing reference sequence passed to dnadiff.
    vcf_file1_in = input VCF file corresponding to dnadiff reference sequence, to be verified.
    vcf_file2_in = input VCF file corresponding to dnadiff query, to be verified.
    vcf_reference_file = reference sequence file that was used to make vcf_file_in.
    outprefix = prefix of output files.
    flank_length = length of "truth" sequence to take before/after alleles when mapping.

    Writes 3 files:
    outprefix.1.sam = mapping of seqs + flanks from dnadiff reference sequence to seqs + flanks for first vcf file.
    outprefix.2.sam = mapping of seqs + flanks from dnadiff query sequence to seqs + flanks for second vcf file.
    outprefix.vcf = VCF file with annotations for validation
    outprefix.stats.tsv = summary stats (see dict output by
                          _parse_sam_file_and_update_vcf_records_and_gather_stats()
                          for a description)'''
    def __init__(self, dnadiff_snps_file, dnadiff_file1, dnadiff_file2, vcf_file_in1, vcf_file_in2, vcf_reference_file, outprefix, flank_length=31, merge_length=None, filter_and_cluster_vcf=True, discard_ref_calls=True, allow_flank_mismatches=True):
        self.dnadiff_snps_file = os.path.abspath(dnadiff_snps_file)
        self.dnadiff_file1 = os.path.abspath(dnadiff_file1)
        self.dnadiff_file2 = os.path.abspath(dnadiff_file2)
        self.vcf_file_in1 = os.path.abspath(vcf_file_in1)
        self.vcf_file_in2 = os.path.abspath(vcf_file_in2)
        self.vcf_reference_file = os.path.abspath(vcf_reference_file)
        self.sam_file_out1 = os.path.abspath(outprefix + '.1.sam')
        self.sam_file_out2 = os.path.abspath(outprefix + '.2.sam')
        self.seqs_out_dnadiff1 = os.path.abspath(outprefix + '.dnadiff1.fa')
        self.seqs_out_dnadiff2 = os.path.abspath(outprefix + '.dnadiff2.fa')
        self.filtered_vcf1 = os.path.abspath(outprefix + '.1.filter.vcf')
        self.filtered_vcf2 = os.path.abspath(outprefix + '.2.filter.vcf')
        self.clustered_vcf1 = os.path.abspath(outprefix + '.1.filter.cluster.vcf')
        self.clustered_vcf2 = os.path.abspath(outprefix + '.2.filter.cluster.vcf')
        self.seqs_out_vcf1 = os.path.abspath(outprefix + '.vcf1.fa')
        self.seqs_out_vcf2 = os.path.abspath(outprefix + '.vcf2.fa')
        self.sam_summary = os.path.abspath(outprefix + '.summary.tsv')
        self.stats_out = os.path.abspath(outprefix + '.stats.tsv')
        self.gt_conf_hist_out = os.path.abspath(outprefix + '.gt_conf_hist.tsv')

        self.flank_length = flank_length
        self.merge_length = flank_length if merge_length is None else merge_length
        self.filter_and_cluster_vcf = filter_and_cluster_vcf
        self.discard_ref_calls = discard_ref_calls
        self.allow_flank_mismatches = allow_flank_mismatches

        if self.filter_and_cluster_vcf:
            self.vcf_to_check1 = self.clustered_vcf1
            self.vcf_to_check2 = self.clustered_vcf2
        else:
            self.vcf_to_check1 = self.vcf_file_in1
            self.vcf_to_check2 = self.vcf_file_in2

    @classmethod
    def _write_dnadiff_plus_flanks_to_fastas(cls, dnadiff_file, ref_infile, query_infile, ref_outfile, query_outfile, flank_length):
        '''Given a dnadiff snps file and the corresponding ref and query fasta,
        write a fasta for each infile containing each variant plus flank_length
        nucleotides added to its start and end.
        Calls each sequence:
            dnadiff_snps_index.start_position.'''
        seq1 = ""
        with open(ref_infile, "r") as in_handle1:
            for record in SeqIO.parse(in_handle1, "fasta"):
                seq1 = str(record.seq)
                break

        seq2 = ""
        with open(query_infile, "r") as in_handle2:
            for record in SeqIO.parse(in_handle2, "fasta"):
                seq2 = str(record.seq)
                break

        out_handle1 = open(ref_outfile, "w")
        out_handle2 = open(query_outfile, "w")

        snps = pd.read_table(dnadiff_file, header=None)

        for line in snps.itertuples():
            print(line)
            assert(len(line) > 4)
            seq_name = str(line[0]) + "." + str(line[1])
            flanked_seq = ""
            if line[2] == '.':
                flanked_seq = seq1[line[1] - flank_length:line[1] + flank_length]
            else:
                flanked_seq = seq1[line[1] - flank_length - 1:line[1] + flank_length]
            print('>' + seq_name, flanked_seq, sep='\n', file=out_handle1)
            if line[3] == '.':
                flanked_seq = seq2[line[4] - flank_length:line[4] + flank_length]
            else:
                flanked_seq = seq2[line[4] - flank_length - 1:line[4] + flank_length]
            print('>' + seq_name, flanked_seq, sep='\n', file=out_handle2)

        out_handle1.close()
        out_handle2.close()

    @classmethod
    def _filter_vcf_for_clustering(cls, infile, outfile, discard_ref_calls=True):
        header_lines, vcf_records = vcf_file_read.vcf_file_to_dict(infile, sort=True, homozygous_only=False, remove_asterisk_alts=True, remove_useless_start_nucleotides=True)

        with open(outfile, 'w') as f:
            print(*header_lines, sep='\n', file=f)
            for ref_name in vcf_records:
                for vcf_record in vcf_records[ref_name]:
                    if vcf_record.FILTER == 'MISMAPPED_UNPLACEABLE':
                        continue
                    if vcf_record.FORMAT is None or 'GT' not in vcf_record.FORMAT:
                        logging.warning('No GT in vcf record:' + str(vcf_record))
                        continue
                    if vcf_record.REF in [".", ""]:
                        continue

                    genotype = vcf_record.FORMAT['GT']
                    genotypes = genotype.split('/')
                    called_alleles = set(genotypes)
                    if len(called_alleles) != 1 or (discard_ref_calls and called_alleles == {'0'}) or '.' in called_alleles:
                        continue

                    if len(vcf_record.ALT) > 1:
                        if called_alleles != {'0'}:
                            vcf_record.set_format_key_value('GT', '1/1')
                            try:
                                vcf_record.ALT = [vcf_record.ALT[int(genotypes[0]) - 1]]
                            except:
                                raise Error('BAD VCf line:' + str(vcf_record))
                        else:
                            vcf_record.set_format_key_value('GT', '0/0')
                            vcf_record.ALT = [vcf_record.ALT[0]]
                    if vcf_record.ALT[0] in [".",""]:
                        continue

                    if vcf_record.FORMAT['GT'] == '0':
                        vcf_record.FORMAT['GT'] = '0/0'
                    elif vcf_record.FORMAT['GT'] == '1':
                        vcf_record.FORMAT['GT'] = '1/1'

                    if 'GL' in vcf_record.FORMAT.keys() and 'GT_CONF' not in vcf_record.FORMAT.keys():
                        likelihoods = vcf_record.FORMAT['GL'].split(',')
                        assert(len(likelihoods) > 2)
                        if called_alleles == {'0'}:
                            vcf_record.set_format_key_value('GT_CONF',str(float(likelihoods[0]) - float(likelihoods[1])))
                        else:
                            vcf_record.set_format_key_value('GT_CONF', str(float(likelihoods[int(genotypes[0])]) - float(likelihoods[0])))
                    if 'SupportFraction' in vcf_record.INFO.keys() and 'GT_CONF' not in vcf_record.FORMAT.keys():
                        vcf_record.set_format_key_value('GT_CONF',
                                                        str(float(vcf_record.INFO['SupportFraction'])*100))
                    print(vcf_record, file=f)


    @classmethod
    def _write_vars_plus_flanks_to_fasta(cls, outfile, vcf_records, ref_seqs, flank_length):
        '''Given a dict of vcf records made by vcf_file_read.vcf_file_to_dict(),
        and its correcsponding file of reference sequences, writes a new fasta file
        of each ref seq and inferred variant sequence plus flank_length nucleotides added to
        its start and end. Calls each sequence:
            ref_name.start_position.vcf_list_index.allele_number
        where allele_numbers in same order as VCF, with ref seq = allele 0.'''
        with open(outfile, 'w') as f:
            for ref_name in sorted(vcf_records):
                for i, vcf_record in enumerate(vcf_records[ref_name]):
                    start_position, alleles = vcf_record.inferred_var_seqs_plus_flanks(ref_seqs[ref_name], flank_length)
                    for allele_index, allele_seq in enumerate(alleles):
                        seq_name = '.'.join([ref_name, str(start_position + 1), str(i), str(allele_index)])
                        print('>' + seq_name, allele_seq, sep='\n', file=f)


    @classmethod
    def _map_seqs_to_seqs(cls, seqs_file_ref, seqs_file_query, outfile):
        '''Map seqs_file to ref_file using BWA MEM.
        Output is SAM file written to outfile'''
        bwa_binary = dependencies.find_binary('bwa')
        command = ' '.join([
            bwa_binary, 'index',
            seqs_file_ref,
        ])
        utils.syscall(command)
        command = ' '.join([
            bwa_binary, 'mem',
            '-a', # report all mappings
            '-Y', # use soft clipping for supplementary alignments
            seqs_file_ref,
            seqs_file_query,
            '>', outfile,
        ])
        utils.syscall(command)

    @classmethod
    def _check_if_sam_match_is_good(cls, sam_record, ref_seqs, flank_length, query_sequence=None, allow_mismatches=True):
        if sam_record.is_unmapped:
            return False

        if not allow_mismatches:
            try:
                nm = sam_record.get_tag('NM')
            except:
                raise Error('No NM tag found in sam record:' + str(sam_record))

            all_mapped = len(sam_record.cigartuples) == 1 and sam_record.cigartuples[0][0] == 0
            return all_mapped and nm == 0

        # don't allow too many soft clipped bases
        if (sam_record.cigartuples[0][0] == 4 and sam_record.cigartuples[0][1] > 3) or (sam_record.cigartuples[-1][0] == 4 and sam_record.cigartuples[-1][1] > 3):
            return False

        if query_sequence is None:
            query_sequence = sam_record.query_sequence
        assert query_sequence is not None

        assert sam_record.reference_name in ref_seqs

        # if the query is short, which happens when the variant we
        # are checking is too near the start or end of the ref sequence
        if len(query_sequence) < 2 * flank_length + 1:
            # This is an edge case. We don't really know which part
            # of the query seq we're looking for, so guess
            length_diff = 2 * flank_length - len(query_sequence)

            if sam_record.query_alignment_start < 5:
                alt_seq_end = len(query_sequence) - flank_length - 1
                alt_seq_start = min(alt_seq_end, flank_length - length_diff)
            else:
                alt_seq_start = flank_length
                alt_seq_end = max(alt_seq_start, length_diff + len(query_sequence) - flank_length - 1)
        else:
            alt_seq_start = flank_length
            alt_seq_end = len(query_sequence) - flank_length - 1

        aligned_pairs = sam_record.get_aligned_pairs()
        wanted_aligned_pairs = []
        current_pos = 0

        i = 0
        while i < len(query_sequence):
            if aligned_pairs[i][0] is None:
                if alt_seq_start - 1 <= current_pos <= alt_seq_end + 1:
                    wanted_aligned_pairs.append(aligned_pairs[i])
            elif current_pos > alt_seq_end:
                break
            else:
                current_pos = aligned_pairs[i][0]
                if alt_seq_start - 1 <= current_pos <= alt_seq_end + 1:
                    wanted_aligned_pairs.append(aligned_pairs[i])

            i += 1

        assert len(wanted_aligned_pairs) > 0

        for pair in wanted_aligned_pairs:
            if None in pair or query_sequence[pair[0]] != ref_seqs[sam_record.reference_name][pair[1]]:
                return False

        return True

    @classmethod
    def _parse_sam_file_and_vcf(cls,samfile, vcffile, flank_length, allow_mismatches):
        found = []
        gt_conf = []
        samfile_handle = pysam.AlignmentFile(samfile, "r")
        sam_previous_record_name = None
        for sam_record in samfile_handle.fetch(until_eof=True):
            if sam_record.query_name == sam_previous_record_name:
                continue
            sam_previous_record_name = sam_record.query_name
            found_conf = False
            found_allele = False
            good_match = DnadiffMappingBasedVerifier._check_if_sam_match_is_good(sam_record,
                                                                                 [sam_record.reference_name],
                                                                                 flank_length,
                                                                                 query_sequence=sam_record.query_sequence,
                                                                                 allow_mismatches=allow_mismatches)
            if good_match:
                ref_name, expected_start, vcf_record_index, allele_index = sam_record.reference_name.rsplit('.', maxsplit=3)
                vcf_reader = pysam.VariantFile(vcffile)
                for i, vcf_record in enumerate(vcf_reader.fetch(ref_name, expected_start + flank_length - 2, expected_start + flank_length + 2)):
                    if i == vcf_record_index:
                        if 'GT' in vcf_record.FORMAT and len(set(vcf_record.FORMAT['GT'].split('/'))) == 1:
                            if allele_index == vcf_record.FORMAT['GT'].split('/')[0]:
                                found.append('1')
                                found_allele = True
                                if 'GT_CONF' in vcf_record.FORMAT:
                                    gt_conf.append(vcf_record.FORMAT['GT_CONF'])
                                    found_conf = True
            if not found_allele:
                found.append('0')
            if not found_conf:
                gt_conf.append(None)
        assert len(found) == len(gt_conf)
        return found, gt_conf

    @classmethod
    def _parse_sam_files(cls, dnadiff_file, samfile1, samfile2, vcffile1, vcffile2, outfile, flank_length, allow_mismatches=True):
        '''Input is the original dnadiff snps file of sites we are searching for
        and 2 SAM files made by _map_seqs_to_seqs(), which show mappings of snp sites
        from from the dnadiff snps file to the vcf (i.e. searches if VCF contains an record
        with the appropriate sequence.
        Creates a tsv detailing whether the snp difference could be detected and at what
        GT_CONF threshold.
        '''

        snps = pd.read_table(dnadiff_file, header=None)
        ref_found, ref_conf = DnadiffMappingBasedVerifier._parse_sam_file_and_vcf(samfile1, vcffile1, flank_length, allow_mismatches)
        query_found, query_conf = DnadiffMappingBasedVerifier._parse_sam_file_and_vcf(samfile2, vcffile2, flank_length, allow_mismatches)
        assert len(snps[0]) == len(ref_found) and len(snps[0]) == len(query_found)
        out_df = pd.DataFrame({'id': snps[0],
                               'ref': snps[2],
                               'alt': snps[3],
                               'ref_found': ref_found,
                               'ref_conf' : ref_conf,
                               'query_found': query_found,
                               'query_conf': query_conf})
        out_df.to_csv(outfile, sep='\t')

    @classmethod
    def _gather_stats(cls, tsv_file):
        stats = {x: 0 for x in ['total', 'found_vars', 'missed_vars']}
        gt_conf_hist = {}

        snps = pd.read_table(tsv_file)
        for line in snps.itertuples():
            stats['total'] += 1
            if line[4] == "1" or line[6] == "1":
                stats['found_vars'] += 1
                gt_confs = set(line[5],line[7]).remove(None)
                gt_conf = max([int(float(i)) for i in gt_confs])
                gt_conf_hist[gt_conf] = gt_conf_hist.get(gt_conf, 0) + 1
            else:
                stats['missed_vars'] += 1
        return stats, gt_conf_hist

    def run(self):
        # Write files of sequences to search for in each vcf
        DnadiffMappingBasedVerifier._write_dnadiff_plus_flanks_to_fastas(self.dnadiff_snps_file, self.dnadiff_file1, self.dnadiff_file1, self.seqs_out_dnadiff1, self.seqs_out_dnadiff2, self.flank_length)

        # Cluster together variants in each vcf
        if self.filter_and_cluster_vcf:
            DnadiffMappingBasedVerifier._filter_vcf_for_clustering(self.vcf_file_in1, self.filtered_vcf1, self.discard_ref_calls)
            DnadiffMappingBasedVerifier._filter_vcf_for_clustering(self.vcf_file_in2, self.filtered_vcf2, self.discard_ref_calls)
            if self.discard_ref_calls:
                clusterer1 = vcf_clusterer.VcfClusterer([self.filtered_vcf1], self.vcf_reference_file, self.clustered_vcf1, merge_method='simple', max_distance_between_variants=self.merge_length)
                clusterer2 = vcf_clusterer.VcfClusterer([self.filtered_vcf2], self.vcf_reference_file, self.clustered_vcf2, merge_method='simple', max_distance_between_variants=self.merge_length)
            else:
                clusterer1 = vcf_clusterer.VcfClusterer([self.filtered_vcf1], self.vcf_reference_file, self.clustered_vcf1, merge_method='gt_aware', max_distance_between_variants=self.merge_length)
                clusterer2 = vcf_clusterer.VcfClusterer([self.filtered_vcf2], self.vcf_reference_file, self.clustered_vcf2, merge_method='gt_aware', max_distance_between_variants=self.merge_length)
            clusterer1.run()
            clusterer2.run()

        vcf_header, vcf_records1 = vcf_file_read.vcf_file_to_dict(self.vcf_to_check1, sort=True, remove_useless_start_nucleotides=True)
        vcf_header, vcf_records2 = vcf_file_read.vcf_file_to_dict(self.vcf_to_check2, sort=True, remove_useless_start_nucleotides=True)
        sample_from_header = vcf_file_read.get_sample_name_from_vcf_header_lines(vcf_header)
        if sample_from_header is None:
            sample_from_header = 'sample'
        vcf_ref_seqs = {}
        pyfastaq.tasks.file_to_dict(self.vcf_reference_file, vcf_ref_seqs)

        DnadiffMappingBasedVerifier._write_vars_plus_flanks_to_fasta(self.seqs_out_vcf1, vcf_records1, vcf_ref_seqs, self.flank_length)
        DnadiffMappingBasedVerifier._write_vars_plus_flanks_to_fasta(self.seqs_out_vcf2, vcf_records2, vcf_ref_seqs, self.flank_length)
        DnadiffMappingBasedVerifier._map_seqs_to_seqs(self.seqs_out_vcf1, self.seqs_out_dnadiff1, self.sam_file_out1)
        DnadiffMappingBasedVerifier._map_seqs_to_seqs(self.seqs_out_vcf2, self.seqs_out_dnadiff2, self.sam_file_out2)
        os.unlink(self.seqs_out_dnadiff1)
        os.unlink(self.seqs_out_dnadiff2)
        os.unlink(self.seqs_out_vcf1)
        os.unlink(self.seqs_out_vcf2)

        DnadiffMappingBasedVerifier._parse_sam_files(self.dnadiff_snps_file, self.sam_file_out1, self.sam_file_out2, self.vcf_to_check1, self.vcf_to_check2, self.sam_summary, self.flank_length, allow_mismatches=self.allow_flank_mismatches)
        stats, gt_conf_hist = DnadiffMappingBasedVerifier._gather_stats(self.sam_summary)

        # write stats file
        with open(self.stats_out, 'w') as f:
            keys = stats.keys()
            print(*keys, sep='\t', file=f)
            print(*[stats[x] for x in keys], sep='\t', file=f)


        # write GT_CONF histogram files
        with open(self.gt_conf_hist_out, 'w') as f:
            print('GT_CONF\tCount', file=f)
            for gt_conf, count in sorted(gt_conf_hist.items()):
                print(gt_conf, count, sep='\t', file=f)
