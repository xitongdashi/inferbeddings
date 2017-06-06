#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse

import os
import sys

import numpy as np
import tensorflow as tf

from flask import Flask, request, jsonify
from flask.views import View

from inferbeddings.nli.util import SNLI, count_trainable_parameters, train_tokenizer_on_instances, to_dataset
from inferbeddings.nli import ConditionalBiLSTM, FeedForwardDAM, FeedForwardDAMP, ESIMv1

import logging

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger(os.path.basename(sys.argv[0]))

app = Flask('nli-service')


class InvalidAPIUsage(Exception):
    """
    Class used for handling error messages.
    """
    DEFAULT_STATUS_CODE = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        self.status_code = self.DEFAULT_STATUS_CODE
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv


@app.errorhandler(InvalidAPIUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


def main(argv):
    def formatter(prog):
        return argparse.HelpFormatter(prog, max_help_position=100, width=200)

    argparser = argparse.ArgumentParser('NLI Service', formatter_class=formatter)

    argparser.add_argument('--train', '-t', action='store', type=str, default='data/snli/snli_1.0_train.jsonl.gz')
    argparser.add_argument('--valid', '-v', action='store', type=str, default='data/snli/snli_1.0_dev.jsonl.gz')
    argparser.add_argument('--test', '-T', action='store', type=str, default='data/snli/snli_1.0_test.jsonl.gz')

    argparser.add_argument('--model', '-m', action='store', type=str, default='cbilstm',
                           choices=['cbilstm', 'ff-dam', 'ff-damp', 'esim1'])

    argparser.add_argument('--embedding-size', action='store', type=int, default=300)
    argparser.add_argument('--representation-size', action='store', type=int, default=200)
    argparser.add_argument('--hidden-size', action='store', type=int, default=200)

    argparser.add_argument('--semi-sort', action='store_true')
    argparser.add_argument('--fixed-embeddings', '-f', action='store_true')
    argparser.add_argument('--normalized-embeddings', '-n', action='store_true')
    argparser.add_argument('--use-masking', action='store_true')
    argparser.add_argument('--prepend-null-token', action='store_true')

    argparser.add_argument('--restore', '-r', action='store', type=str, default=None)

    args = argparser.parse_args(argv)

    train_path, valid_path, test_path = args.train, args.valid, args.test

    model_name = args.model

    embedding_size = args.embedding_size
    representation_size = args.representation_size

    is_fixed_embeddings = args.fixed_embeddings
    use_masking = args.use_masking
    prepend_null_token = args.prepend_null_token

    restore_path = args.restore

    logger.debug('Reading corpus ..')
    train_instances, dev_instances, test_instances = SNLI.generate(
        train_path=train_path, valid_path=valid_path, test_path=test_path)

    logger.info('Train size: {}\tDev size: {}\tTest size: {}'.format(len(train_instances), len(dev_instances), len(test_instances)))

    logger.debug('Parsing corpus ..')

    num_words = None
    all_instances = train_instances + dev_instances + test_instances
    qs_tokenizer, a_tokenizer = train_tokenizer_on_instances(all_instances, num_words=num_words)

    neutral_idx = a_tokenizer.word_index['neutral'] - 1
    entailment_idx = a_tokenizer.word_index['entailment'] - 1
    contradiction_idx = a_tokenizer.word_index['contradiction'] - 1

    vocab_size = qs_tokenizer.num_words if qs_tokenizer.num_words else len(qs_tokenizer.word_index) + 1

    sentence1_ph = tf.placeholder(dtype=tf.int32, shape=[None, None], name='sentence1')
    sentence2_ph = tf.placeholder(dtype=tf.int32, shape=[None, None], name='sentence2')

    sentence1_length_ph = tf.placeholder(dtype=tf.int32, shape=[None], name='sentence1_length')
    sentence2_length_ph = tf.placeholder(dtype=tf.int32, shape=[None], name='sentence2_length')

    embedding_layer = tf.get_variable('embeddings', shape=[vocab_size, embedding_size],
                                      initializer=tf.contrib.layers.xavier_initializer(),
                                      trainable=not is_fixed_embeddings)

    sentence1_embedding = tf.nn.embedding_lookup(embedding_layer, sentence1_ph)
    sentence2_embedding = tf.nn.embedding_lookup(embedding_layer, sentence2_ph)

    dropout_keep_prob_ph = tf.placeholder(tf.float32, name='dropout_keep_prob')

    model_kwargs = dict(
        sequence1=sentence1_embedding, sequence1_length=sentence1_length_ph,
        sequence2=sentence2_embedding, sequence2_length=sentence2_length_ph,
        representation_size=representation_size, dropout_keep_prob=dropout_keep_prob_ph)

    RTEModel = None
    if model_name == 'cbilstm':
        RTEModel = ConditionalBiLSTM
    elif model_name == 'ff-dam':
        ff_kwargs = dict(use_masking=use_masking, prepend_null_token=prepend_null_token)
        model_kwargs.update(ff_kwargs)
        RTEModel = FeedForwardDAM
    elif model_name == 'ff-damp':
        ff_kwargs = dict(use_masking=use_masking, prepend_null_token=prepend_null_token)
        model_kwargs.update(ff_kwargs)
        RTEModel = FeedForwardDAMP
    elif model_name == 'esim1':
        ff_kwargs = dict(use_masking=use_masking)
        model_kwargs.update(ff_kwargs)
        RTEModel = ESIMv1

    assert RTEModel is not None
    model = RTEModel(**model_kwargs)

    with tf.Session() as session:
        saver = tf.train.Saver()
        logger.debug('Total parameters: {}'.format(count_trainable_parameters()))
        saver.restore(session, restore_path)

        class Service(View):
            methods = ['GET', 'POST']

            def dispatch_request(self):

                sentence1 = request.form['sentence1'] if 'sentence1' in request.form else request.args.get('sentence1')
                sentence2 = request.form['sentence2'] if 'sentence1' in request.form else request.args.get('sentence2')

                if 'sentence1' in request.form:
                    sentence1 = request.form['sentence1']
                if 'sentence2' in request.form:
                    sentence2 = request.form['sentence2']

                sentence1_seq = qs_tokenizer.texts_to_sequences([sentence1])
                sentence2_seq = qs_tokenizer.texts_to_sequences([sentence2])

                sentence1_seq = [item for sublist in sentence1_seq for item in sublist]
                sentence2_seq = [item for sublist in sentence2_seq for item in sublist]

                # Compute answer
                feed_dict = {
                    sentence1_ph: [sentence1_seq],
                    sentence2_ph: [sentence2_seq],
                    sentence1_length_ph: [len(sentence1_seq)],
                    sentence2_length_ph: [len(sentence2_seq)],
                    dropout_keep_prob_ph: 1.0
                }

                predictions = session.run(tf.nn.softmax(model.logits), feed_dict=feed_dict)[0]
                answer = {
                    'neutral': str(predictions[neutral_idx]),
                    'contradiction': str(predictions[contradiction_idx]),
                    'entailment': str(predictions[entailment_idx])
                }

                return jsonify(answer)

        app.add_url_rule('/v1/nli', view_func=Service.as_view('request'))

        app.run(host='0.0.0.0', port=8889, debug=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main(sys.argv[1:])
