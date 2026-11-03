import csv
import math
from dataclasses import replace

import pytest

from beam_lab.config import Config, Serviceability, plan_rows
from beam_lab.pipeline import run
from beam_lab.dataset import size_labels
from beam_lab.serviceability import (cracked_section, effective_inertia, deflection_curve,
                                    peak, evaluate, XI)


def configuration(h=600, **settings):
    return Config.parse(dict(scenarios=[dict(name="Service example", spans_m=[6], dead_kN_m=[20],
        live_kN_m=[10], fc_MPa=30, fy_MPa=420, centroid_cover_mm=60)], sections_mm=[[300,h]],
        loading_protocol="research_full_udl", dead_load_basis="total_including_self_weight",
        serviceability=dict(enabled=True, category="beam_sensitive", sustained_live_fraction=.25,
                           duration_months=60, attachment_timing="after_immediate_dead", **settings)))


def data(config):
    base = next(plan_rows(config))
    rows = []
    for name, q in (("SLS_D", 20), ("SLS_DL", 30), ("SLS_SUSTAINED", 22.5)):
        for i in range(21):
            x = i*300.
            rows.append(dict(combination=name, station_mm=x, Mu3_kNm=q*x*(6000-x)/2/1e6,
                             Vu2_kN=q*(x-3000)/1000, Pu_kN=0, Mu2_kNm=0, Vu3_kN=0, Tu_kNm=0))
    design = [dict(station_mm=i*300., As_top_mm2=500., As_bottom_mm2=2000., error="", warning="") for i in range(21)]
    return base, rows, design


@pytest.mark.parametrize("fixed,denominator", [(False,384/5), (True,384)])
def test_integrated_udl_curve_matches_closed_form_bending_and_shear(fixed, denominator):
    length, ec, ie, b, h, q = 6000., 30000., 4e9, 300., 600., 20.
    m = -q*length**2/12 if fixed else 0.
    curve = deflection_curve(length,m,m,q,ec,ie,b*h)
    magnitude, x, signed = peak(curve,length)
    expected = q*length**4/(denominator*ec*ie)+q*length**2/(8*(5/6)*(ec/2.4)*b*h)
    assert magnitude == pytest.approx(expected, rel=1e-11)
    assert x == pytest.approx(length/2)
    assert signed > 0


def test_peak_search_finds_off_midspan_maximum():
    curve = deflection_curve(6000,0,-180e6,40,30000,4e9,180000)
    magnitude, x, _ = peak(curve,6000)
    assert 2000 < x < 3000
    assert magnitude > abs(sum(curve[i]*.5**i for i in range(5)))


def test_transformed_section_matches_independent_singly_reinforced_solution():
    b,h,cover,as_,ec = 300.,600.,60.,2000.,30000.
    n,d = 200000/ec,h-cover
    c = (-n*as_+math.sqrt((n*as_)**2+2*b*n*as_*d))/b
    icr, actual, rho = cracked_section(b,h,cover,0,as_,ec)
    assert actual == pytest.approx(c)
    assert icr == pytest.approx(b*c**3/3+n*as_*(d-c)**2)
    assert rho == 0
    assert cracked_section(b,h,cover,500,as_,ec)[0] > icr


def test_aci_2019_inertia_threshold_and_time_factors():
    assert effective_inertia(1000,200,90,60) == 1000
    assert effective_inertia(1000,200,90,120) == pytest.approx(250)
    assert XI == {3:1.,6:1.2,12:1.4,60:2.}


def test_service_pass_fail_and_no_invented_dead_or_total_code_limits():
    for h, expected in ((800,"PASS"),(400,"FAIL")):
        config = configuration(h)
        result, checks, stations = evaluate(*data(config),config.serviceability)
        assert result["sls_aci_status"] == result["sls_overall_status"] == expected, result
        assert result["sls_dead_immediate_status"] == "NO_CODE_LIMIT"
        assert result["sls_combined_immediate_status"] == "NO_CODE_LIMIT"
        assert len(checks) == 3 and len(stations) == 21
        assert stations[0]["sls_dl_signed_mm"] == pytest.approx(0)
        assert stations[-1]["sls_dl_signed_mm"] == pytest.approx(0, abs=1e-10)
        assert result["sls_lambda"] == pytest.approx(2/(1+50*result["sls_compression_ratio"]))
        assert result["sls_live_increment_mm"] == pytest.approx(result["sls_combined_immediate_mm"]-result["sls_dead_immediate_mm"])


def test_attachment_timing_changes_movement_and_project_checks_are_separate():
    config = configuration(800)
    base,fs,ds = data(config)
    before = replace(config.serviceability, attachment_timing="before_service_loading", dead_limit_divisor=100000/10)
    after = evaluate(base,fs,ds,config.serviceability)[0]
    result = evaluate(base,fs,ds,before)[0]
    assert result["sls_after_attachment_mm"] > after["sls_after_attachment_mm"]
    assert result["sls_aci_status"] == "PASS"
    assert result["sls_project_status"] == "FAIL" and result["sls_overall_status"] == "FAIL"


def test_unattached_live_check_requires_no_invented_sustained_fraction():
    config = configuration(800)
    setting = Serviceability(enabled=True, category="floor_unattached")
    setting.require_ready()
    result,checks,_ = evaluate(*data(config), setting)
    assert result["sls_aci_live_limit_mm"] == 6000/360
    assert result["sls_aci_status"] == "PASS"
    assert "sls_total_long_term_mm" not in result
    result = evaluate(*data(config),replace(setting,category="roof_unattached"))[0]
    assert result["sls_aci_live_limit_mm"] == 6000/180


def test_missing_or_unsupported_data_is_review_never_fabricated_pass():
    config = configuration()
    base,fs,ds = data(config)
    assert evaluate(base,[],ds,config.serviceability)[0]["sls_aci_status"] == "REVIEW"
    ds[10]["As_bottom_mm2"] = None
    assert evaluate(base,fs,ds,config.serviceability)[0]["sls_aci_pass"] is None
    ds[10]["As_bottom_mm2"] = 2000
    ds[0]["error"] = "Section error"
    assert evaluate(base,fs,ds,config.serviceability)[0]["sls_aci_status"] == "REVIEW"
    ds[0]["error"] = ""
    fs[0]["Mu3_kNm"] = 100
    assert "diagram" in evaluate(base,fs,ds,config.serviceability)[0]["sls_reason"]
    assert evaluate(*data(configuration(1600)),config.serviceability)[0]["sls_aci_status"] == "REVIEW"


def test_nonsensitive_attachment_tolerance_caps_the_aci_limit():
    config = configuration(800)
    settings = replace(config.serviceability,category="beam_nonsensitive",attachment_tolerance_mm=1.)
    result,checks,_ = evaluate(*data(config),settings)
    assert result["sls_aci_after_attachment_limit_mm"] == 1.
    assert result["sls_aci_status"] == "FAIL"
    with pytest.raises(ValueError,match="tolerance"):
        replace(settings,attachment_tolerance_mm=None).require_ready()


def test_service_settings_validate_code_options_and_require_project_information():
    for settings in (dict(category="invented"), dict(sustained_live_fraction=1.1), dict(duration_months=24), dict(dead_limit_divisor=0)):
        with pytest.raises(ValueError):
            Serviceability.parse(settings)
    with pytest.raises(ValueError,match="Choose"):
        Serviceability(enabled=True).require_ready()
    with pytest.raises(ValueError,match="fraction"):
        Serviceability(enabled=True,category="beam_sensitive").require_ready()
    with pytest.raises(ValueError,match="attached"):
        Serviceability(enabled=True,category="beam_sensitive",sustained_live_fraction=0,duration_months=60).require_ready()


def test_pipeline_exports_service_data_and_preserves_strength_labels(tmp_path):
    config = configuration(800)
    class Adapter:
        version="test"
        close_error=None
        def __init__(self,*args): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def build(self,*args,**kwargs):
            assert kwargs['serviceability'].enabled
            return ['B1'], ['U_1p2D_1p6L_FULL'], ['LAB_DEAD','LAB_LIVE']
        def analyze(self,cases): return [dict(case=c,status=4) for c in cases]
        def forces(self,frame,combos):
            rows=[]
            for combo in combos:
                q={'SLS_D':20.,'SLS_DL':30.,'SLS_SUSTAINED':22.5,'U_1p2D_1p6L_FULL':40.}[combo]
                for i in range(21):
                    x=i*.3
                    rows.append(dict(object='B1',element='B1',station_m=x,element_station_m=x,
                                     combination=combo,step_type='',step_number=0,P_kN=0,V2_kN=q*(x-3),
                                     V3_kN=0,T_kNm=0,M2_kNm=0,M3_kNm=q*x*(6-x)/2))
            return rows
        def start_design(self): pass
        def design(self,frame):
            return [dict(frame='B1',location_m=i*.3,top_combo='U',top_area_m2=.0005,
                    bottom_combo='U',bottom_area_m2=.002,shear_combo='U',shear_area_m2_per_m=.00025,
                    torsion_long_combo='U',torsion_long_area_m2=0,torsion_trans_combo='U',
                    torsion_trans_area_m2_per_m=0,error='',warning='') for i in range(21)]
        def combination_designs(self,frames,combos,progress):
            return {combos[0]:dict(designs={'B1':self.design('B1')},flexure=[dict(UniqueName='B1',Status=None)],shear=[dict(UniqueName='B1',Status=None)])}
        def table_catalog(self): return []
    manifest=run(config,tmp_path,adapter_factory=Adapter)
    assert manifest['state']=='completed'
    def read(name):
        with (tmp_path/name).open(encoding='utf-8-sig') as f: return list(csv.DictReader(f))
    ann=read('ann_beams.csv')[0]
    assert ann['section_overall_status']=='PASS' and ann['sls_aci_status']=='PASS'
    assert ann['serviceability_classification_eligible']=='1'
    assert len(read('forces.csv'))==21 and len(read('service_forces.csv'))==63
    assert len(read('deflection_checks.csv'))==3 and len(read('deflection_stations.csv'))==21
    assert read('size_targets_sls.csv')[0]['preliminary_h_mm']=='800'
