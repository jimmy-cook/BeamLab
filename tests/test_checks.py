from beam_lab.checks import feasibility, overall


def requirement():
    return dict(As_top_mm2=500, As_bottom_mm2=500, Av_over_s_mm2_per_m=250, Al_torsion_mm2=0, At_over_s_mm2_per_m=0, error="", warning="")

def force():
    return dict(Mu3_kNm=100, Vu2_kN=50, Tu_kNm=0, Pu_kN=0, Mu2_kNm=0, Vu3_kN=0)

def test_etabs_action_specific_evidence_and_unknown_errors():
    design = [dict(requirement(), error="Reinforcing required exceeds maximum allowed")]
    result = feasibility([force()], design, [{"Status": "O/S T"}], [{"Status": None}])
    assert result["section_moment_status"] == "FAIL"
    assert result["section_shear_status"] == "PASS"
    assert result["section_torsion_status"] == "NOT_REQUIRED"
    assert result["section_overall_status"] == "FAIL"
    design[0]["error"] = "Unknown design error"
    result = feasibility([force()], design, [{"Status": None}], [{"Status": None}])
    assert result["section_moment_status"] == "REVIEW"
    assert result["section_shear_status"] == "REVIEW"
    assert result["section_overall_status"] == "FAIL"



def test_flexure_envelope_can_omit_a_station_reinforcement_limit_marker():
    design = [dict(requirement(), error="Reinforcing required exceeds maximum allowed")]
    result = feasibility([force()], design, [{"Status": None}], [{"Status": None}])
    assert result["section_moment_status"] == "FAIL"
    assert result["section_shear_status"] == "PASS"
    assert result["section_overall_status"] == "FAIL"


def test_missing_results_are_not_passes():
    assert overall([]) == "NOT_EVALUATED"
    assert feasibility([], [], [], [])["section_overall_status"] == "NOT_EVALUATED"
