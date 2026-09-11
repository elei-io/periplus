"""Disposable layout bench; synthetic defaults are not a production sizing proof."""
import argparse
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import hashlib
import json
import resource
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from periplus.materialization.registry import BY_NAME
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.tokenization import term_counts
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.benchmarking import _measure, load_case


def run(count, input_dir, type_partition=False, term="monkey"):
    started = perf_counter()
    if input_dir:
        sources = [p.read_bytes().decode('utf-8') for p in sorted(input_dir.glob('*.html'))[:count]]
        if len(sources) != count:
            raise ValueError('input directory must contain the requested number of HTML files')
    else:
        sources = [f'<body><h1>Document {i}</h1>' + ''.join(
            f'<p class="price">Monthly mon<strong>key</strong> price {j} café 日本語</p>'
            for j in range(40)) + '</body>' for i in range(count)]
    parsed = {hashlib.sha256(s.encode()).hexdigest(): parse_document(s) for s in sources}
    context = VisitBatchContext((), (), (), {k: v[1] for k, v in parsed.items()},
                               {k: v[0] for k, v in parsed.items()}, {}, frozenset(parsed))
    terms = BY_NAME['term'].rows(context)
    context.dictionary_ids['term'] = {row['text']: i for i, row in enumerate(terms.to_pylist(), 1)}
    tables = {name: BY_NAME[name].rows(context) for name in ('html_nodes', 'prose', 'content_posting', 'node_posting')}
    preparation = perf_counter() - started
    preparation_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for text in context.search_text_by_content.values():
        assert text.content_counts == term_counts(text.prose), "document token semantics changed"
    legacy = pa.Table.from_pylist([
        dict(content_id=key, node_index=e.element_index, parent_index=e.parent_index,
             subtree_end_index=e.subtree_end_index, sibling_index=e.child_index, depth=e.depth,
             tag=e.tag.lower(), namespace=e.namespace_uri, attributes=list(e.attributes.items()), text_direct=e.text_direct)
        for key, (_, elements) in parsed.items() for e in elements
    ], schema=pa.schema([('content_id',pa.string()),('node_index',pa.int32()),('parent_index',pa.int32()),
                         ('subtree_end_index',pa.int32()),('sibling_index',pa.int32()),('depth',pa.int32()),
                         ('tag',pa.string()),('namespace',pa.string()),('attributes',pa.map_(pa.string(),pa.string())),('text_direct',pa.string())]))
    with TemporaryDirectory(prefix='periplus-node-layout-') as directory:
        root = Path(directory)
        sizes = {}
        old_nodes = tables['html_nodes'].drop(['tag', 'attributes', 'text_direct'])
        for name, table in {**tables, 'old_nodes': old_nodes, 'old_elements': legacy}.items():
            pq.write_table(table, root/f'{name}.parquet', compression='zstd')
            sizes[name] = (root/f'{name}.parquet').stat().st_size
        config = CatalogueConfig('periplus',str(root/'metadata.duckdb'),str(root/'data'),'ducklake')
        with Catalogue(config, duckdb_config={'memory_limit': '512MB', 'threads': '2'}) as catalogue:
            catalogue.bootstrap()
            db = catalogue.trusted_connection
            if type_partition:
                db.execute("ALTER TABLE material.html_nodes SET PARTITIONED BY (node_type)")
            for name, table in tables.items():
                db.register('batch',table)
                db.execute(f'INSERT INTO material.{name} SELECT * FROM batch')
            db.register('dictionary_batch', pa.table({'text': list(context.dictionary_ids['term']), 'term_id': list(context.dictionary_ids['term'].values())}))
            db.execute('INSERT INTO material.term SELECT * FROM dictionary_batch')
            db.register('legacy',legacy)
            db.execute('CREATE TABLE material.old_elements AS SELECT * FROM legacy LIMIT 0')
            db.execute('ALTER TABLE material.old_elements SET PARTITIONED BY (bucket(8, content_id))')
            db.execute('ALTER TABLE material.old_elements SET SORTED BY (content_id, node_index)')
            db.execute('INSERT INTO material.old_elements SELECT * FROM legacy')
            db.register('legacy_nodes',old_nodes)
            db.execute('CREATE TABLE material.old_nodes AS SELECT * FROM legacy_nodes LIMIT 0')
            db.execute('ALTER TABLE material.old_nodes SET PARTITIONED BY (bucket(8, content_sha256))')
            db.execute('ALTER TABLE material.old_nodes SET SORTED BY (content_sha256, node_index)')
            db.execute('INSERT INTO material.old_nodes SELECT * FROM legacy_nodes')
            case = load_case(Path(__file__).resolve().parents[1]/'cases/node-layout')
            candidate = case.sql.replace("'monkey'", "'" + term.replace("'", "''") + "'").replace('FROM term_node','FROM public_v1.term_node').replace('JOIN html_node','JOIN public_v1.html_node').replace('JOIN html_element','JOIN public_v1.html_element')
            baseline = candidate.replace('public_v1.html_element','material.old_elements').replace('public_v1.html_node', '(SELECT content_sha256 AS content_id,node_index,parent_index,node_type FROM material.old_nodes)')
            results = []
            with patch('periplus.query.benchmarking.catalogue_config_from_env',return_value=config):
                db.execute('BEGIN')
                try:
                    for order in [('separate','unified'),('unified','separate')]:
                        pair = {name: asdict(_measure(db, replace(case,sql=baseline if name=='separate' else candidate),None,1)) for name in order}
                        assert pair['separate']['result_digest'] == pair['unified']['result_digest']
                        results.append(pair)
                finally:
                    db.execute('ROLLBACK')
    return {'document_terms_verified':True,'type_partition':type_partition,'documents':len(parsed),'synthetic':input_dir is None,'source_bytes':sum(len(s.encode()) for s in sources), 'projection_rows':{name: table.num_rows for name,table in tables.items()},'preparation_seconds':preparation,
            'preparation_max_rss_platform_units':preparation_rss,
            'max_rss_platform_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'parquet_bytes':sizes,'pairs':results}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--documents',type=int,default=500)
    parser.add_argument('--input-dir',type=Path)
    parser.add_argument('--type-partition',action='store_true')
    parser.add_argument('--term',default='monkey')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not 1 <= args.documents <= 1000:
        parser.error('documents must be between 1 and 1000')
    result=run(args.documents,args.input_dir,args.type_partition,args.term)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='pairs'}))
