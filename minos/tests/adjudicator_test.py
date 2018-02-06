import shutil
import os
import unittest

from minos import adjudicator

modules_dir = os.path.dirname(os.path.abspath(adjudicator.__file__))
data_dir = os.path.join(modules_dir, 'tests', 'data', 'adjudicator')

class TestAdjudicator(unittest.TestCase):
    def test_run(self):
        '''test run'''
        # We're just testing that it doesn't crash.
        # Check the output files exist, but not their contents.
        outdir = 'tmp.adjudicator.out'
        if os.path.exists(outdir):
            shutil.rmtree(outdir)
        ref_fasta = os.path.join(data_dir, 'run.ref.fa')
        reads_file = os.path.join(data_dir, 'run.bwa.bam')
        vcf_files =  [os.path.join(data_dir, x) for x in ['run.calls.1.vcf', 'run.calls.2.vcf']]
        adj = adjudicator.Adjudicator(outdir, ref_fasta, reads_file, vcf_files)
        adj.run()
        self.assertTrue(os.path.exists(outdir))
        self.assertTrue(os.path.exists(adj.log_file))
        self.assertTrue(os.path.exists(adj.final_vcf))
        self.assertTrue(os.path.exists(adj.gramtools_outdir))
        self.assertTrue(os.path.exists(adj.clustered_vcf))
        shutil.rmtree(outdir)
