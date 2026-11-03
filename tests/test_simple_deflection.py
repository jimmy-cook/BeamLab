import csv
from dataclasses import replace

import pytest

from beam_lab.config import Config, Serviceability, plan_rows
from beam_lab.ann_export import ann_deflection, ann_size_wide as ann_size
from beam_lab.dataset import summarize
from beam_lab.pipeline import run
from beam_lab.serviceability import evaluate


def fixture(basis="immediate", dl=120, total=240):
    config = Config.parse(dict(scenarios=[dict(name="Simple limits",spans_m=[6,6],dead_kN_m=[20,20],live_kN_m=[10,10])],
        sections_mm=[[300,600]],loading_protocol="research_full_udl",dead_load_basis="total_including_self_weight",
        serviceability=dict(enabled=True,limit_mode="user_defined",total_basis=basis,dead_limit_divisor=dl,total_limit_divisor=total)))
    base=next(plan_rows(config))
    forces=[]
    for name,q in (("SLS_D",20),("SLS_DL",30),("SLS_SUSTAINED",22.5)):
        for i in range(21):
            x=i*300.
            forces.append(dict(combination=name,station_mm=x,Mu3_kNm=q*x*(6000-x)/2/1e6,
                               Vu2_kN=q*(x-3000)/1000,Pu_kN=0,Mu2_kNm=0,Vu3_kN=0,Tu_kNm=0))
    design=[dict(station_mm=i*300.,As_top_mm2=500.,As_bottom_mm2=2000.,Av_over_s_mm2_per_m=250.,
                 Al_torsion_mm2=0.,At_over_s_mm2_per_m=0.,error="",warning="") for i in range(21)]
    return config,base,forces,design


def test_simple_limits_need_no_aci_category_or_time_inputs():
    config,base,fs,ds=fixture()
    config.serviceability.require_ready()
    result,checks,_=evaluate(base,fs,ds,config.serviceability)
    assert result["sls_overall_status"]=="PASS",result
    assert result["sls_aci_status"]=="NOT_EVALUATED" and result["sls_aci_pass"] is None
    assert result["sls_dead_limit_mm"]==50 and result["sls_total_limit_mm"]==25
    assert len(checks)==2 and {c["basis"] for c in checks}=={"USER_DEFINED_LIMIT"}
    assert result["sls_total_checked_mm"]==result["sls_combined_immediate_mm"]
    assert result["sls_dead_pass"]==result["sls_total_pass"]==result["sls_pass"]==1


def test_changing_either_limit_changes_only_its_label_not_the_deflection():
    config,base,fs,ds=fixture()
    original=evaluate(base,fs,ds,config.serviceability)[0]
    for changed,failed in ((dict(dead_limit_divisor=10000),"dead"),(dict(total_limit_divisor=10000),"total")):
        result=evaluate(base,fs,ds,replace(config.serviceability,**changed))[0]
        assert result[f"sls_{failed}_pass"]==0 and result["sls_pass"]==0
        assert result["sls_total_checked_mm"]==original["sls_total_checked_mm"]
        assert result["sls_dead_checked_mm"]==original["sls_dead_checked_mm"]
        assert result["sls_settings_id"]!=original["sls_settings_id"]


def test_at_limit_is_pass_and_over_limit_is_fail():
    config,base,fs,ds=fixture()
    calculated=evaluate(base,fs,ds,config.serviceability)[0]["sls_total_checked_mm"]
    divisor=base["span_length_mm"]/calculated
    assert evaluate(base,fs,ds,replace(config.serviceability,total_limit_divisor=divisor))[0]["sls_total_status"]=="PASS"
    assert evaluate(base,fs,ds,replace(config.serviceability,total_limit_divisor=divisor*1.01))[0]["sls_total_status"]=="FAIL"


def test_long_term_total_has_required_inputs_and_compares_the_correct_curve():
    config,base,fs,ds=fixture("long_term")
    with pytest.raises(ValueError,match="sustained"):
        config.serviceability.require_ready()
    settings=replace(config.serviceability,sustained_live_fraction=.25,duration_months=60)
    settings.require_ready()
    result,checks,_=evaluate(base,fs,ds,settings)
    assert result["sls_total_checked_mm"]==result["sls_total_long_term_mm"]
    assert result["sls_total_checked_mm"]>result["sls_combined_immediate_mm"]
    assert checks[1]["curve"]=="TOTAL_LONG_TERM"
    assert result["sls_dead_checked_mm"]==result["sls_dead_immediate_mm"]


def test_simple_settings_validate_modes_and_require_both_limits():
    for invalid in (dict(limit_mode="unknown"),dict(total_basis="unknown"),dict(dead_limit_divisor=0)):
        with pytest.raises(ValueError): Serviceability.parse(invalid)
    with pytest.raises(ValueError,match="both"):
        Serviceability(enabled=True,limit_mode="user_defined").require_ready()
    assert Serviceability.parse(dict(enabled=False)).limit_mode=="aci"


def test_unavailable_data_and_disabled_calculation_never_get_binary_fail_labels():
    config,base,fs,ds=fixture()
    result=evaluate(base,[],ds,config.serviceability)[0]
    assert result["sls_project_status"]==result["sls_overall_status"]=="REVIEW"
    assert result["sls_pass"] is None and result["sls_aci_status"]=="NOT_EVALUATED"
    result=evaluate(base,fs,ds,replace(config.serviceability,enabled=False))[0]
    assert result["sls_overall_status"]=="NOT_EVALUATED" and result["sls_enabled"]==0
    assert result["sls_dead_limit_divisor"]==120


def test_compact_export_contains_full_span_context_and_explicit_user_limit_targets():
    config,base,fs,ds=fixture()
    summary=summarize(base,[],ds)
    summary.update(evaluate(base,fs,ds,config.serviceability)[0])
    row=ann_deflection([summary])[0]
    assert row["limit_source"]=="USER_DEFINED" and row["total_deflection_basis"]=="immediate"
    assert row["span_1_mm"]==row["span_2_mm"]==6000 and row["span_3_mm"]==0
    assert row["span_index"]==0 and row["dl_pass"]==row["total_pass"]==row["deflection_pass"]==1
    assert row["dl_limit_mm"]==50 and row["total_limit_mm"]==25
    assert row["classification_eligible"]==1 and row["aci_status"]=="NOT_EVALUATED"
    assert row["sample_id"].endswith(row["settings_id"])
    assert row["reinforcement_basis"]=="ETABS_required_idealized_centroid"


def test_sizing_export_requires_every_span_to_pass_and_chooses_one_smallest_target():
    config,_,_,_=fixture()
    data=config.to_dict();data['sections_mm']=[[300,600],[350,600]]
    config=Config.parse(data)
    beams=[]
    for base in plan_rows(config):
        beams.append(dict(base,split='train',etabs_summary_clear=1,analysis_status='complete',design_status='summary_clear',
                          section_overall_status='PASS',section_moment_status='PASS',section_shear_status='PASS',
                          sls_overall_status='PASS',sls_settings_id='settings',sls_limit_mode='user_defined',sls_total_basis='immediate'))
    rows=ann_size(beams)
    assert len(rows)==1 and rows[0]['target_b_mm']==300
    assert 'Mu_kNm' not in rows[0] and 'As_top_mm2' not in rows[0]
    beams[1]['sls_overall_status']='FAIL'
    assert ann_size(beams)[0]['target_b_mm']==350
    for row in beams:
        row['section_shear_status']='FAIL'
    assert ann_size(beams)==[]
