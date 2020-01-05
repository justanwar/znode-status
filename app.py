#!/usr/bin/env python3
import threading
import time
import timeit
import sched

import re
from flask import Flask, render_template, request, session, redirect, flash, jsonify, Response, abort
from uuid import uuid4

from werkzeug import serving

from models import db
from ZCoinAdapter import ZCoinAdapter, SyncingException
from datetime import timedelta, datetime
from adapters.BittrexAdapter import BittrexAdapter

app = Flask(__name__)

# Load settings from the config file
app.config.from_pyfile('znode.cfg')

if app.config['DEBUG']:
    app.config['TEMPLATES_AUTO_RELOAD'] = True

zcoin = ZCoinAdapter(**app.config['ZCOIN_RPC_CONFIG'])

cache_data = {}


@app.context_processor
def inject_debug():
    return dict(DEBUG=app.debug)

def get_count():
    return zcoin.call('znode', 'count')

def get_template():
    return zcoin.call('getblocktemplate')

def get_znodelistfull():
    return zcoin.call('znodelist', 'full')

def get_znodelistqualify():
    return zcoin.call('znodelist', 'qualify')

def get_znodelistrank():
    return zcoin.call('znodelist', 'rank')

def get_price():
    btr = BittrexAdapter()
    btc_usd = btr.get_price('USDT_BTC')
    xzc_btc = btr.get_price('BTC_XZC')
    return xzc_btc * btc_usd

def _cache(name, func=None, mins=60):
    global cache_data
    if func:
        if name not in cache_data:
            cache_data[name] = {}
        cache_data[name]['last_update'] = datetime.utcnow()
        try:
            cache_data[name]['data'] = func()
        except Exception as e:
            print('Caught exception while fetching {}: {}'.format(name, e))
            pass
    elif name in cache_data:
        if datetime.utcnow() < cache_data[name]['last_update'] + timedelta(minutes=mins):
            return cache_data[name]['data']
@app.route('/')
def index():
    return render_template('index.html', couchdb=app.config['PUBLIC_COUCHDB'])

@app.route('/api/znode/count')
def znode_count():
    return str(_cache('block_count'))

@app.route('/api/xzc_price')
def price():
    return str(_cache('xzc_price'))

@app.route('/api/getblocktemplate')
def getblocktemplate():
    return jsonify(_cache('blocktemplate'))

@app.route('/api/getznodelist')
def getznodelist():
    return jsonify(_cache('znodelistfull'))

@app.route('/api/getznodelist/qualify')
def getznodelistqualify():
    return jsonify(_cache('znodelistqualify'))

@app.route('/api/getznodelist/rank')
def getznodelistrank():
    return jsonify(_cache('znodelistrank'))

@app.route('/api2/znode/<string:payee>')
def getznode(payee):
    znodelistfull = _cache('znodelistfull')
    znodelistqualify = _cache('znodelistqualify')
    znodelistrank = _cache('znodelistrank')
    for k, v in znodelistfull.items():
        v = v.strip()
        if re.match('\w+\s\d+\s' + payee + '\s\d+\s', v):
            znode = {}
            v = re.split('\s+', v)
            znode['status'] = v[0]
            znode['protocol'] = v[1]
            znode['payee'] = v[2]
            znode['lastseen'] = v[3]
            znode['activeseconds'] = v[4]
            znode['lastpaidtime'] = v[5]
            znode['lastpaidblock'] = v[6]
            znode['IP'] = v[7]
            znode['txid'] = re.match('COutPoint\((\w+), (\d+)\)', k).group(1)
            znode['qualify_str'] = znodelistqualify[k]
            znode['qualified'] = znodelistqualify[k] == 'true'
            znode['qualify_reason'] = znodelistqualify[k]
            znode['rank'] = znodelistrank[k] if k in znodelistrank else 'N/A'
            znode['queue_pos'] = get_queuepos(k, znodelistfull, znodelistqualify)
            return jsonify(znode)
    return abort(404)

def get_queuepos(key, znodelistfull, znodelistqualify):
    queue = []
    for k, v in znodelistfull.items():
        if 'ENABLED' in v:
            v = re.split('\s+', v)
            qualified_weight = pow(10, 64)
            lastpaidblock_weight = pow(10, 9)
            qualified_score = 0 if znodelistqualify[k] == 'true' else 1
            lastpaidblock_score = int(v[6])
            txid_score = int(lastpaidblock_weight * int(re.match('COutPoint\((\w+), (\d+)\)', k).group(1), 16) / int('f' * 64, 16))
            # queue score is comprised of three weighted ints, used for determining a node's queue position
            # the lower the queue score, the more likely to be paid
            queue_score = qualified_score * qualified_weight + lastpaidblock_score * lastpaidblock_weight + txid_score
            queue.append((k, queue_score))
    queue.sort(key=lambda t: t[1]) # sort by queue score
    for idx, q in enumerate(queue):
        if q[0] == key:
            return idx
    return -1

def refresh_cache():
    _cache('blocktemplate', func=get_template)
    _cache('block_count', func=get_count)
    _cache('xzc_price', func=get_price)
    _cache('znodelistfull', func=get_znodelistfull)
    _cache('znodelistqualify', func=get_znodelistqualify)
    _cache('znodelistrank', func=get_znodelistrank)
    t = threading.Timer(50, refresh_cache)
    t.start()

if __name__ == "__main__":
    if not serving.is_running_from_reloader():
        t = threading.Timer(2, refresh_cache)
        t.start()
    db.init_app(app)
    app.secret_key = app.config['SECRET_KEY']
    with app.app_context():
        # if the tables already exist, this shouldn't cause any issues.
        db.create_all()
        app.run(debug=app.config['DEBUG'], port=app.config['PORT'])
