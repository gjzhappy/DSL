#!/usr/bin/env python3
from __future__ import annotations
import sys
from graph_phone_common import load_doc, graph, path, check_frontend_checkvalid_schema, check_ifslot_validate_contract

def must(p,msg):
    if not p: raise AssertionError(msg)
def main():
    d=load_doc(); nodes,title,out,inc=graph(d)
    check_frontend_checkvalid_schema(d)
    check_ifslot_validate_contract(d)
    p1=path(title,out,nodes,'IF_是否缺槽','结束_返回填槽请求','need_slot',{'代码执行_构建QueryPlan','HTTP请求_执行Mongo查询','代码执行_准备报告LLM输入','LLM_生成竞分对比报告_本地版'})
    must(p1,'缺槽问题未进入 fill -> ansslot 或错误进入查询链路')
    p2=path(title,out,nodes,'IF_是否缺槽','代码执行_构建QueryPlan','success')
    must(p2,'槽位完整未进入 QueryPlan')
    p3=path(title,out,nodes,'IF_output_type是否为报告类','结束_返回最终回答','false',{'代码执行_准备报告LLM输入','LLM_生成竞分对比报告_本地版','代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'})
    must(p3,'普通非报告问题未避开报告/full LLM 到 final')
    p4=path(title,out,nodes,'IF_output_type是否为报告类','代码执行_准备报告LLM输入','report')
    must(p4,'报告问题未进入 report_input')
    p5=path(title,out,nodes,'IF_满血版LLM开关','LLM_生成竞分对比报告_本地版','false',{'代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'})
    must(p5,'ENABLE_FULL_LLM=false 未进入本地 LLM')
    p6=path(title,out,nodes,'IF_满血版LLM开关','HTTP请求_调用满血版LLM接口','enabled')
    must(p6 and '代码执行_准备满血版LLM请求' in p6,'ENABLE_FULL_LLM=true 且 token ok 未进入 full_req/http_full')
    p7=path(title,out,nodes,'IF_满血版Token是否成功','LLM_生成竞分对比报告_本地版','false',{'代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'})
    must(p7,'token 失败未进入本地 LLM 或错误进入 full_req/http_full')
    p8=path(title,out,nodes,'IF_满血版LLM是否成功','LLM_生成竞分对比报告_本地版','false')
    must(p8,'full LLM 失败未进入本地 LLM')
    for name,p in [('缺槽',p1),('槽位完整',p2),('非报告',p3),('报告',p4),('LLM关闭',p5),('LLM开启token ok',p6),('token fail',p7),('full fail',p8)]:
        print('PASS',name,'BFS:',' -> '.join(p))
if __name__=='__main__':
    try: main()
    except Exception as e: print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
