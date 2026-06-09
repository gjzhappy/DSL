#!/usr/bin/env python3
import copy
import json
from schema_metadata_c_mvp_lib import (
    extract_schema_metadata, build_alias_index, resolve_field_name, resolve_metric_name,
    resolve_field_label, resolve_metric_label, build_chart_title, prettify_field_name,
)

ORDERS_MD = '''# orders

## Schema Metadata
```yaml
metadata_type: mongo_schema
metadata_version: c_mvp_v1
schema_version: "2026-03-19"
database: shennong
collection_name: orders
collection_label: 订单
collection_aliases:
  - 订单
  - 订单表
primary_key: order_id
default_time_field: created_at
fields:
  - name: order_id
    label: 订单ID
    type: string
    aliases:
      - 订单编号
  - name: status
    label: 订单状态
    type: enum
    role: dimension
    aliases:
      - 订单状态
      - 履约状态
  - name: amount
    label: 订单金额
    type: number
    aliases:
      - 销售额
metrics:
  - name: order_count
    label: 订单数
    function: count
    field: order_id
    source_fields:
      - order_id
    output_type: number
    aliases:
      - 订单数
      - 单量
      - 订单量
  - name: gmv_sum
    label: 销售额
    function: sum
    field: amount
    aliases:
      - 销售额
relations:
  - target_collection: products
    relation_type: enrich
    join_keys:
      - source_field: product_name
        target_field: product_name
        match_type: exact
```
'''

PRODUCTS_MD = '''# products

## Schema Metadata
```yaml
metadata_type: mongo_schema
metadata_version: c_mvp_v1
collection_name: products
collection_label: 商品
collection_aliases:
  - 商品
primary_key: product_id
default_time_field: updated_at
fields:
  - name: status
    label: 商品状态
    aliases:
      - 商品状态
  - name: product_name
    label: 商品名称
metrics:
  - name: product_count
    label: 商品数
    function: count
    field: product_id
    aliases:
      - 商品数
```
'''

PCB_MD = '''## Schema Metadata
```yaml
metadata_type: mongo_schema
metadata_version: c_mvp_v1
collection_name: pcb_layers
collection_label: PCB板层
fields:
  - name: layer
    label: 板层
metrics:
  - name: via_count
    label: 过孔数量
```
'''


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    schema = extract_schema_metadata(ORDERS_MD + '\n' + PRODUCTS_MD)
    orders = schema['collections']['orders']
    products = schema['collections']['products']
    assert_eq(orders['collection_name'], 'orders', 'orders collection_name')
    assert_eq(orders['collection_label'], '订单', 'orders collection_label')
    assert_eq(orders['primary_key'], 'order_id', 'orders primary_key')
    assert_eq(orders['default_time_field'], 'created_at', 'orders default_time_field')
    assert_eq(orders['fields']['status']['label'], '订单状态', 'orders status label')
    assert_true('订单状态' in orders['fields']['status']['aliases'], 'orders status aliases include 订单状态')
    assert_true('履约状态' in orders['fields']['status']['aliases'], 'orders status aliases include 履约状态')
    assert_eq(orders['metrics']['order_count']['label'], '订单数', 'orders order_count label')
    assert_eq(orders['relations'][0]['join_keys'][0]['source_field'], 'product_name', 'relation source_field')

    idx = build_alias_index(schema)['aliases']
    assert_eq(idx['订单状态'][0]['collection'] + '.' + idx['订单状态'][0]['name'], 'orders.status', 'alias 订单状态')
    assert_eq(idx['履约状态'][0]['collection'] + '.' + idx['履约状态'][0]['name'], 'orders.status', 'alias 履约状态')
    assert_eq(idx['订单数'][0]['collection'] + '.' + idx['订单数'][0]['name'], 'orders.order_count', 'alias 订单数')
    assert_true(any(x['collection']=='orders' and x['name'] in {'gmv_sum','amount'} for x in idx['销售额']), 'alias 销售额 retains candidates')
    assert_true(len(idx['销售额']) >= 2, 'alias 销售额 not overwritten')
    assert_eq([x for x in idx['商品状态'] if x['collection']=='products'][0]['name'], 'status', 'alias 商品状态')
    assert_eq([x for x in idx['商品数'] if x['collection']=='products'][0]['name'], 'product_count', 'alias 商品数')

    r = resolve_field_name('订单状态', schema, 'orders')
    assert_eq(r['resolved'], 'status', 'resolve 订单状态')
    r = resolve_field_name('商品状态', schema, 'products')
    assert_eq(r['resolved'], 'status', 'resolve 商品状态 in products')
    assert_eq(r['collection'], 'products', 'resolve 商品状态 collection')
    m = resolve_metric_name('订单数', schema, 'orders')
    assert_eq(m['resolved'], 'order_count', 'resolve metric 订单数')

    no_alias = copy.deepcopy(schema)
    no_alias['collections']['orders']['fields']['status']['aliases'] = []
    no_alias['collections']['orders']['fields']['status']['label'] = ''
    unresolved = resolve_field_name('订单状态', no_alias, 'orders')
    assert_eq(unresolved['resolved'], '订单状态', 'unresolved alias is preserved')
    assert_true(unresolved['warning'], 'unresolved alias emits warning')

    title, source = build_chart_title('status', 'order_count', schema, 'orders')
    assert_eq(title, '按订单状态统计订单数', 'chart title uses schema labels')
    pcb = extract_schema_metadata(PCB_MD)
    pcb_title, _ = build_chart_title('layer', 'via_count', pcb, 'pcb_layers')
    assert_eq(pcb_title, '按板层统计过孔数量', 'non commerce chart title uses schema labels')
    forbidden = ['订单数', '销售额', '品牌', '厂商']
    assert_true(not any(x in pcb_title for x in forbidden), 'non commerce title has no business fallback words')

    assert_eq(resolve_field_label('status', schema, 'orders'), '订单状态', 'final answer field label')
    assert_eq(resolve_metric_label('order_count', schema, 'orders'), '订单数', 'final answer metric label')
    empty_schema = {'collections': {'orders': {'collection_name':'orders','fields': {'status': {'name':'status'}}, 'metrics': {'order_count': {'name':'order_count'}}}}, 'warnings': []}
    assert_eq(resolve_field_label('status', empty_schema, 'orders'), 'status', 'no schema field label fallback')
    assert_eq(resolve_metric_label('order_count', empty_schema, 'orders'), 'order count', 'no schema metric label fallback')

    # deterministic patch sentinel: keep C-MVP helpers pure and not patch-related.
    assert_eq(prettify_field_name('relative_days'), 'relative days', 'prettify does not affect deterministic patch')
    print('schema_metadata_c_mvp self-test passed')

if __name__ == '__main__':
    main()
