"""Actual local MCP transport, not direct function mocks."""
import asyncio
import json
import os
import sys
from pathlib import Path
import ezdxf
import pytest


def test_stdio_inventory_and_project_proposal(tmp_path):
    pytest.importorskip('mcp')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    source = tmp_path / 'different_site.dxf'
    doc = ezdxf.new()
    doc.units = 4
    doc.layers.new('SITE_HOT_WATER')
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': 'SITE_HOT_WATER'})
    doc.saveas(source)
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'model.geometry.json'

    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(root/'mep_mcp_server.py')],
                                       cwd=str(root), env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {'inspect_mep_source','propose_mep_profile','get_mep_diagnostics'} <= names
                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError
                    return '\n'.join(block.text for block in result.content if block.type == 'text')
                inventory = json.loads(await call('inspect_mep_source', {'dxf_path': str(source)}))
                parsed = await call('parse_dxf', {'dxf_path':str(source),'json_out_path':str(output),'use_ai':False,'use_vision':False})
                assert 'Parsed' in parsed, parsed
                current = json.loads(await call('get_mep_profile', {'json_path':str(output)}))
                profile = {'version':1,'source_sha256':inventory['source_sha256'], 'layers':[
                    {'pattern':'SITE_HOT_WATER','category':'pipe','system':'heating','representation':'centerline',
                     'diameter_mm':19,'dimension_basis':'user','placement':'center','center_elevation_mm':91.5}]}
                proposal = json.loads(await call('propose_mep_profile', {'profile':profile,
                    'expected_revision':current['revision'],'json_path':str(output)}))
                assert 'proposal_id' in proposal, proposal
                unchanged = json.loads(await call('get_mep_profile', {'json_path':str(output)}))
                assert unchanged['revision'] == current['revision']
                assert unchanged['sources'][0]['mep_profile'] is None
                refused = json.loads(await call('apply_mep_profile_proposal', {'proposal_id':proposal['proposal_id'],
                    'expected_revision':current['revision'],'json_path':str(output)}))
                assert refused['error'] == 'user_review_required'
                result = json.loads(await call('get_mep_diagnostics', {'json_path':str(output)}))
                assert 'mep_diagnostics' in result
    asyncio.run(asyncio.wait_for(check(), 90))
