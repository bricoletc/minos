#!/usr/bin/env bash
set -e

./run.make_refs.py
pre=run
ref=$pre.ref.0.fa
bwa index $ref

for i in 1 2; do
    reads1=$pre.reads.$i.1.fq
    reads2=$pre.reads.$i.2.fq
    fastaq to_perfect_reads --seed 42 $pre.ref.$i.fa -  230 1 20 100 | fastaq deinterleave - $reads1 $reads2
    bwa mem -R "@RG\tID:sample.$i\tSM:sample.$i" $ref $reads1 $reads2 | samtools sort -o $pre.reads.$i.sorted.bam
    samtools mpileup -ugf $ref $pre.reads.$i.sorted.bam | bcftools call -vm -O v -o $pre.calls.$i.vcf
    samtools index $pre.reads.$i.sorted.bam
done

rm $ref.*
rm $pre.ref.{1,2}.fa

awk '/^#CHROM/ {print "##minos_max_read_length=242"} 1' $pre.calls.1.vcf > tmp.$$
mv tmp.$$ $pre.calls.1.vcf
