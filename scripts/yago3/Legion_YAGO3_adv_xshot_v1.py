#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import itertools
import os
import os.path

import sys
import argparse
import logging


def cartesian_product(dicts):
    return (dict(zip(dicts, x)) for x in itertools.product(*dicts.values()))


def summary(configuration):
    kvs = sorted([(k, v) for k, v in configuration.items()], key=lambda e: e[0])
    return '_'.join([('%s=%s' % (k, v)) for (k, v) in kvs])


def to_cmd(c, _path=None):
    if _path is None:
        _path = '/home/ucacmin/workspace/inferbeddings/'
    command = 'python3 {}/bin/kbp-cli.py' \
              ' --train {}/data/yago3_mte10_5k/yago3_mte10-train.tsv.gz' \
              ' --valid {}/data/yago3_mte10_5k/yago3_mte10-valid.tsv.gz' \
              ' --test {}/data/yago3_mte10_5k/yago3_mte10-test.tsv.gz' \
              ' --clauses {}/data/yago3_mte10_5k/clauses/clauses_B=100_C=0.8.pl' \
              ' --nb-epochs {}' \
              ' --lr {}' \
              ' --nb-batches {}' \
              ' --model {}' \
              ' --similarity {}' \
              ' --margin {}' \
              ' --embedding-size {}' \
              ' --subsample-size {}' \
              ' --loss {}' \
              ' --adv-lr {} --adv-init-ground --adversary-epochs {}' \
              ' --discriminator-epochs {} --adv-weight {} --adv-batch-size {}' \
              ''.format(_path, _path, _path, _path, _path,
                        c['epochs'], c['lr'], c['batches'],
                        c['model'], c['similarity'],
                        c['margin'], c['embedding_size'],
                        c['subsample_size'],
                        c['loss'],
                        c['adv_lr'], c['adv_epochs'],
                        c['disc_epochs'], c['adv_weight'], c['adv_batch_size'])
    return command


def to_logfile(c, path):
    outfile = "%s/Legion_yago3_adv_xshot_v1.%s.log" % (path, summary(c))
    return outfile


def main(argv):
    def formatter(prog):
        return argparse.HelpFormatter(prog, max_help_position=100, width=200)

    argparser = argparse.ArgumentParser('Generating experiments for the Legion cluster', formatter_class=formatter)
    argparser.add_argument('--debug', '-D', action='store_true', help='Debug flag')
    argparser.add_argument('--path', '-p', action='store', type=str, default=None, help='Path')

    args = argparser.parse_args(argv)

    hyperparameters_space_transe = dict(
        epochs=[100],
        optimizer=['adagrad'],
        lr=[.1],
        batches=[10],
        model=['TransE'],
        similarity=['l1', 'l2'],
        margin=[1],  # margin=[1, 2, 5, 10],
        embedding_size=[20, 50, 100, 150, 200],
        loss=['hinge'],
        subsample_size=[.1, .5, 1],
        adv_lr=[.1],
        adv_epochs=[0, 10],
        disc_epochs=[10],
        adv_weight=[0, 1, 100, 10000, 1000000],
        adv_batch_size=[1, 10, 100]
    )

    hyperparameters_space_distmult_complex = dict(
        epochs=[100],
        optimizer=['adagrad'],
        lr=[.1],
        batches=[10],
        model=['DistMult', 'ComplEx'],
        similarity=['dot'],
        margin=[1],  # margin=[1, 2, 5, 10],
        embedding_size=[20, 50, 100, 150, 200],
        loss=['hinge'],
        subsample_size=[.1, .3, .5, 1],
        adv_lr=[.1],
        adv_epochs=[0, 10],
        disc_epochs=[10],
        adv_weight=[0, 1, 100, 10000, 1000000],
        adv_batch_size=[1, 10, 100]
    )

    configurations_transe = cartesian_product(hyperparameters_space_transe)
    configurations_distmult_complex = cartesian_product(hyperparameters_space_distmult_complex)

    path = '/home/ucacmin/Scratch/inferbeddings/logs/Legion_yago3_adv_xshot_v1/'
    if not os.path.exists(path):
        os.makedirs(path)

    configurations = list(configurations_transe) + list(configurations_distmult_complex)

    for job_id, cfg in enumerate(configurations):
        logfile = to_logfile(cfg, path)

        completed = False
        if os.path.isfile(logfile):
            with open(logfile, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                completed = '### MICRO (test filtered)' in content

        if not completed:
            line = '{} >> {} 2>&1'.format(to_cmd(cfg, _path=args.path), logfile)

            if args.debug:
                print(line)
            else:
                file_name = 'Legion_yago3_adv_xshot_v1_{}.job'.format(job_id)
                alias = ''
                job_script = '#!/bin/bash -l\n' \
                             '#$ -wd /home/ucacmin/Scratch/jobs/\n' \
                             '#$ -l mem=4G\n' \
                             '#$ -l h_rt=16:00:00\n' \
                             '{}\n{}\n'.format(alias, line)

                with open(file_name, 'w') as f:
                    f.write(job_script)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main(sys.argv[1:])
