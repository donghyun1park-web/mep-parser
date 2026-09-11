import json
import math
import shutil
import subprocess
import pytest
import geom_contract as gc


def test_mep_section_override_and_legacy_gui_aliases_are_consistent():
    assert gc.mep_dimensions('pipe', {'diameter':100, 'overrides':{'width':15.9}})['diameter'] == 15.9
    assert gc.mep_dimensions('pipe', {'diameter':100, 'overrides':{'width':16,'diameter':20}})['diameter'] == 20
    rec={'width_mm':110,'height_mm':54,'elevation':0,
         'overrides':{'width':204,'height':60,'elevation':2570}}
    assert gc.mep_dimensions('duct',rec)=={'width_mm':204.0,'height_mm':60.0}
    assert gc.z_range('duct',rec)==(2540,2600)


def test_nominal_pipe_size_does_not_override_physical_outside_diameter():
    rec={'diameter':15.9,'nominal_size':'15A','material':'PB'}
    assert gc.mep_dimensions('pipe',rec)['diameter']==15.9
    with pytest.raises(gc.ContractError):
        gc.mep_dimensions('pipe',{'diameter':None,'dimension_status':'unknown'})


def test_floor_heating_and_slab_contact_use_explicit_datums():
    levels={'structural_slab_top_mm':0,'floor_to_floor_mm':2800,'slab_thickness_mm':200}
    floor=[{'role':'impact_insulation','thickness_mm':30},
           {'role':'foamed_concrete','thickness_mm':40},
           {'role':'screed','thickness_mm':40}]
    z=gc.mep_elevation('foam_top',15.9,levels,floor)
    assert z==pytest.approx(77.95)
    assert gc.z_range('pipe',{'diameter':15.9,'elevation':z})==pytest.approx((70,85.9))
    assert gc.mep_elevation('slab_soffit',60,levels,floor)==2570
    assert gc.mep_elevation('slab_soffit',54,levels,floor)==2573
    assert gc.mep_elevation('center',60,levels,[],1234)==1234
    with pytest.raises(gc.ContractError):
        gc.mep_elevation('foam_top',15.9,levels,[])


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),True])
def test_invalid_mep_dimensions_are_not_replaced_with_defaults(value):
    with pytest.raises(gc.ContractError):
        gc.mep_dimensions('pipe',{'diameter':value})


def test_injected_javascript_uses_same_mep_dimensions_and_axis_override():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node unavailable; Python contract remains tested')
    rec={'width_mm':110,'height_mm':54,'elevation':0,'overrides':{'width':204,'height':60,'elevation':2570}}
    script=gc.js_constants()+f"\nconsole.log(JSON.stringify([gcMepDimensions('duct',{json.dumps(rec)}),gcZRange('duct',{json.dumps(rec)})]));"
    actual=json.loads(subprocess.check_output([node,'-e',script],text=True))
    assert actual==[gc.mep_dimensions('duct',rec),list(gc.z_range('duct',rec))]
