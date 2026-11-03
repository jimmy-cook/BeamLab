"""ETABS 22/23 COM adapter. Calls follow the installed CSI API ETABS v1 CHM.

Every API status code is checked. No attachment to a user's open model occurs.
The adapter owns its newly created ETABS process and closes only that process.
"""
from __future__ import annotations

import ctypes
import math
from pathlib import Path

from .config import strength_combinations

SUPPORTED_VERSIONS = ("22", "23")
CO_E_CLASSSTRING = -2147221005  # "Invalid class string": ETABSv1.Helper is not registered


class EtabsError(RuntimeError):
    pass


def registration_hint(executable):
    tool = Path(executable).parent/"RegisterETABS.exe"
    return (f"The ETABS API is not registered in Windows. Right-click {tool} and choose 'Run as administrator', "
            "then start the run again. Run it again whenever another ETABS version or SAFE has been installed or registered.")


def etabs_processes():
    """Process ids of running ETABS.exe (empty if tasklist is unavailable)."""
    import csv
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ETABS.exe", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(row[1]) for row in csv.reader(out.splitlines()) if len(row) > 1 and row[1].isdigit()}


def end_processes(pids):
    import subprocess
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def native_helper(comtypes, executable):
    """The cHelper from the chosen ETABS folder (NativeAPI\\x64), so 22 and 23 each use their own API version.

    Returns (helper, api module), or None when the folder has no native API files."""
    native = Path(executable).parent/"NativeAPI"/"x64"
    host, library = native/"ETABSv1.comhost.dll", native/"ETABSv1.tlb"
    if not (host.is_file() and library.is_file()):
        return None
    from comtypes.server import IClassFactory
    api = comtypes.client.GetModule(str(library))
    factory = ctypes.POINTER(IClassFactory)()
    result = ctypes.WinDLL(str(host)).DllGetClassObject(ctypes.byref(api.Helper._reg_clsid_),
                                                         ctypes.byref(IClassFactory._iid_), ctypes.byref(factory))
    if result != 0:
        raise EtabsError(f"Could not load the ETABS API from {host} (HRESULT {result & 0xFFFFFFFF:#010x})")
    return factory.CreateInstance(interface=api.cHelper), api


def checked(result, operation):
    """comtypes puts ref/in-out arguments first and CSI's integer return last."""
    code = result[-1] if isinstance(result, (tuple, list)) else result
    if isinstance(code, bool) or code != 0:
        raise EtabsError(f"{operation} returned CSI status {code!r}")
    return result[:-1] if isinstance(result, (tuple, list)) else None


def arrays(result, names, operation):
    values = checked(result, operation)
    if values is None or len(values) != len(names) + 1:
        raise EtabsError(f"Unexpected {operation} result layout")
    n, *columns = values
    if not isinstance(n, int) or n < 0 or any(len(x) != n for x in columns):
        raise EtabsError(f"Inconsistent {operation} array lengths")
    return [dict(zip(names, row)) for row in zip(*columns)]


FORCE_NAMES = ["object", "station_m", "element", "element_station_m", "combination",
               "step_type", "step_number", "P_kN", "V2_kN", "V3_kN", "T_kNm", "M2_kNm", "M3_kNm"]
DESIGN_NAMES = ["frame", "location_m", "top_combo", "top_area_m2", "bottom_combo", "bottom_area_m2",
                "shear_combo", "shear_area_m2_per_m", "torsion_long_combo", "torsion_long_area_m2",
                "torsion_trans_combo", "torsion_trans_area_m2_per_m", "error", "warning"]


class Etabs:
    def __init__(self, executable, cache_dir):
        self.executable = str(Path(executable).resolve())
        self.cache_dir = Path(cache_dir).resolve()
        self.app = None
        self.model = None
        self.started = False
        self.version = None
        self.close_error = None
        self.pids = set()  # ETABS processes this adapter started

    def __enter__(self):
        if not Path(self.executable).is_file():
            raise EtabsError(f"ETABS executable not found: {self.executable}")
        import comtypes
        import comtypes.client
        # Uvicorn jobs run on their own single worker thread with its own COM apartment.
        comtypes.CoInitialize()
        self.comtypes = comtypes
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            comtypes.client.gen_dir = str(self.cache_dir)
            found = native_helper(comtypes, self.executable)
            if found:
                helper, api = found
            else:
                try:
                    helper = comtypes.client.CreateObject("ETABSv1.Helper")
                except OSError as exc:
                    if getattr(exc, "winerror", None) == CO_E_CLASSSTRING:
                        raise EtabsError(registration_hint(self.executable)) from exc
                    raise
                import comtypes.gen.ETABSv1 as api
                helper = helper.QueryInterface(api.cHelper)
            before = etabs_processes()
            self.app = helper.CreateObject(self.executable)
            self.pids = etabs_processes() - before
            if not self.app:
                raise EtabsError("ETABS Helper.CreateObject returned no application")
            try:
                checked(self.app.ApplicationStart(), "ApplicationStart")
            except comtypes.COMError as exc:
                # ETABS opens hidden but cannot connect back when its API registration is missing or belongs to another
                # program. ApplicationExit cannot reach it either, so end the process this adapter started (it holds a licence).
                end_processes(self.pids)
                raise EtabsError(f"ETABS started but did not answer the API ({exc.args[1] if len(exc.args) > 1 else exc}). "
                                 + registration_hint(self.executable)) from exc
            self.started = True
            self.model = self.app.SapModel
            self.version = checked(self.model.GetVersion("", 0.0), "GetVersion")[0]
            if str(self.version).split(".")[0] not in SUPPORTED_VERSIONS:
                raise EtabsError(f"This adapter supports ETABS {' and '.join(SUPPORTED_VERSIONS)}; found {self.version}")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.started:
            try:
                checked(self.app.ApplicationExit(False), "ApplicationExit")
            except Exception as exc:
                self.close_error = str(exc)
                # A hung ETABS ignores ApplicationExit; end it so it does not keep a licence. One already closed is gone.
                end_processes(self.pids & etabs_processes())
        self.model = self.app = None
        if hasattr(self, "comtypes"):
            self.comtypes.CoUninitialize()

    def responding(self):
        """False once the ETABS process has been closed, has crashed or no longer answers the API."""
        try:
            return self.model is not None and checked(self.model.GetVersion("", 0.0), "GetVersion") is not None
        except Exception:
            return False

    def call(self, label, function, *args):
        return checked(function(*args), label)

    def build(self, scenario, b_mm, h_mm, stations, model_path,
              loading_protocol="patterned_gravity", dead_load_basis="superimposed_plus_self_weight", serviceability=None):
        m = self.model
        c = self.call
        # eUnits.kN_m_C = 6. Stress values are kN/m2, not MPa.
        c("InitializeNewModel", m.InitializeNewModel, 6)
        c("NewBlank", m.File.NewBlank)
        c("SetPresentUnits", m.SetPresentUnits, 6)
        c("SetStories_2", m.Story.SetStories_2, 0.0, 1, ["BeamLevel"], [3.0],
          [True], [""], [False], [0.0], [-1])
        c("SetMaterial concrete", m.PropMaterial.SetMaterial, "LAB_CONC", 2)
        c("SetMPIsotropic concrete", m.PropMaterial.SetMPIsotropic,
          "LAB_CONC", 4700*math.sqrt(scenario.fc_MPa)*1000, 0.2, 1e-5)
        c("SetWeightAndMass", m.PropMaterial.SetWeightAndMass,
          "LAB_CONC", 1, scenario.concrete_weight_kN_m3)
        c("SetOConcrete", m.PropMaterial.SetOConcrete,
          "LAB_CONC", scenario.fc_MPa*1000, False, 1.0, 1, 2, 0.002, 0.003)
        for name, fy in (("LAB_LONG", scenario.fy_MPa), ("LAB_TIES", scenario.fyt_MPa)):
            c("SetMaterial rebar", m.PropMaterial.SetMaterial, name, 6)
            c("SetMPIsotropic rebar", m.PropMaterial.SetMPIsotropic, name, 200e6, 0.3, 1.17e-5)
            c("SetORebar", m.PropMaterial.SetORebar, name, fy*1000, 1.25*fy*1000,
              fy*1000, 1.25*fy*1000, 1, 1, 0.01, 0.09, False)
        c("SetRectangle", m.PropFrame.SetRectangle, "LAB_BEAM", "LAB_CONC", h_mm/1000, b_mm/1000)
        c("SetRebarBeam", m.PropFrame.SetRebarBeam, "LAB_BEAM", "LAB_LONG", "LAB_TIES",
          scenario.centroid_cover_mm/1000, scenario.centroid_cover_mm/1000, 0., 0., 0., 0.)
        # Same EI reduction on every candidate; no rigid zones, releases or end offsets.
        c("SetModifiers", m.PropFrame.SetModifiers, "LAB_BEAM",
          [1., 1., 1., 1., scenario.flexural_modifier, scenario.flexural_modifier, 1., 1.])
        c("SetCode", m.DesignConcrete.SetCode, "ACI 318-19")
        actual_code = c("GetCode", m.DesignConcrete.GetCode, "")[0]
        if actual_code != "ACI 318-19":
            raise EtabsError(f"Code readback failed: {actual_code}")
        if loading_protocol != "patterned_gravity":
            # CSI's concrete-design preferences can also generate pattern-live
            # effects. Explicitly disable them for the fully loaded research line.
            for item, value in ((10, .9), (13, .75), (16, 0.)):
                c("ACI318_19.SetPreference", m.DesignConcrete.ACI318_19.SetPreference, item, value)
            prefs = self.table("Concrete Frame Design Preferences - ACI 318-19")
            if len(prefs) != 1 or any(abs(float(prefs[0][key])-value) > 1e-9 for key, value in
                                      (("PatLLF", 0.), ("PhiTen", .9), ("PhiShrTor", .75))):
                raise EtabsError("Concrete design preference readback failed for full-UDL research mode")

        # ETABS names are case insensitive (NewBlank may contain 'Dead'). A unique
        # research pattern avoids depending on localized/default pattern names.
        self_weight_multiplier = 0. if dead_load_basis == "total_including_self_weight" else 1.
        c("Add LAB_DEAD", m.LoadPatterns.Add, "LAB_DEAD", 1, self_weight_multiplier, True)
        live_names = [f"LL_{i+1}" for i in range(len(scenario.spans_m))] if loading_protocol == "patterned_gravity" else ["LAB_LIVE"]*len(scenario.spans_m)
        for name in dict.fromkeys(live_names):
            c("Add live pattern", m.LoadPatterns.Add, name, 3, 0., True)

        frames = []
        joints = []
        x = 0.
        for i, length in enumerate(scenario.spans_m):
            name = c("AddByCoord", m.FrameObj.AddByCoord, x, 0., 3., x+length, 0., 3.,
                     "", "LAB_BEAM", f"B{i+1}", "Global")[0]
            frames.append(name)
            pi, pj = c("GetPoints", m.FrameObj.GetPoints, name, "", "")
            if not i:
                joints.append(pi)
            joints.append(pj)
            c("SetOutputStations", m.FrameObj.SetOutputStations, name, 2, 0., stations, False, False, 0)
            c("SetDesignProcedure", m.FrameObj.SetDesignProcedure, name, 2, 0)
            # Framing type 4 = Non-sway. This is a continuous beam on ideal supports.
            c("ACI318_19.SetOverwrite", m.DesignConcrete.ACI318_19.SetOverwrite, name, 1, 4., 0)
            if loading_protocol != "patterned_gravity":
                c("Disable live-load reduction", m.DesignConcrete.ACI318_19.SetOverwrite, name, 2, 1., 0)
            for pattern, load in (("LAB_DEAD", scenario.dead_kN_m[i]), (live_names[i], scenario.live_kN_m[i])):
                c("SetLoadDistributed", m.FrameObj.SetLoadDistributed, name, pattern,
                  1, 10, 0., 1., load, load, "Global", True, True, 0)
            x += length
        for i, joint in enumerate(joints):
            # UY, UZ, RX, RZ restrained; RY free. UX fixed at first support only.
            # Adjacent spans share a joint and retain rotation continuity.
            c("SetRestraint", m.PointObj.SetRestraint, joint, [i == 0, True, True, True, False, True], 0)

        # Strength combinations are LINEAR ADDITIVE, never envelopes.
        # Exhaustive span live-load patterns preserve concurrent P,V,T,M results.
        combos = strength_combinations(len(scenario.spans_m), loading_protocol)
        for name, terms in combos.items():
            c("RespCombo.Add", m.RespCombo.Add, name, 0)
            for case, factor in terms.items():
                c("RespCombo.SetCaseList", m.RespCombo.SetCaseList, name, 0, case, factor)
            c("SetComboStrength", m.DesignConcrete.SetComboStrength, name, True)
        if serviceability and serviceability.enabled:
            for name, factor in (("SLS_D", 0.), ("SLS_DL", 1.), ("SLS_SUSTAINED", serviceability.sustained_live_fraction or 0.)):
                c("Add service combination", m.RespCombo.Add, name, 0)
                c("Set service dead", m.RespCombo.SetCaseList, name, 0, "LAB_DEAD", 1.)
                for live in dict.fromkeys(live_names):
                    if factor:
                        c("Set service live", m.RespCombo.SetCaseList, name, 0, live, factor)
                c("Exclude service combination from strength design", m.DesignConcrete.SetComboStrength, name, False)
        c("SetRunCaseFlag all off", m.Analyze.SetRunCaseFlag, "", False, True)
        for case in dict.fromkeys(["LAB_DEAD", *live_names]):
            c("SetRunCaseFlag", m.Analyze.SetRunCaseFlag, case, True, False)
        c("File.Save", m.File.Save, str(Path(model_path).resolve()))
        return frames, combos, list(dict.fromkeys(["LAB_DEAD", *live_names]))

    def analyze(self, cases):
        self.call("RunAnalysis", self.model.Analyze.RunAnalysis)
        rows = arrays(self.model.Analyze.GetCaseStatus(0, [], []), ["case", "status"], "GetCaseStatus")
        states = {row["case"]: row["status"] for row in rows}
        if any(states.get(case) != 4 for case in cases):
            raise EtabsError(f"Analysis not finished for all requested cases: {states}")
        return rows

    def forces(self, frame, combos):
        m = self.model
        self.call("DeselectAllCasesAndCombosForOutput", m.Results.Setup.DeselectAllCasesAndCombosForOutput)
        for name in combos:
            self.call("SetComboSelectedForOutput", m.Results.Setup.SetComboSelectedForOutput, name, True)
        return arrays(m.Results.FrameForce(frame, 0, 0, *([[]]*13)), FORCE_NAMES, "FrameForce")

    def start_design(self):
        self.call("StartDesign", self.model.DesignConcrete.StartDesign)
        if not self.model.DesignConcrete.GetResultsAvailable():
            raise EtabsError("ETABS has no concrete design results after StartDesign")

    def design(self, frame):
        return arrays(self.model.DesignConcrete.GetSummaryResultsBeam(frame, 0, *([[]]*14), 0),
                      DESIGN_NAMES, "GetSummaryResultsBeam")

    def table_catalog(self):
        """Runtime discovery avoids guessing version-dependent design table keys."""
        return arrays(self.model.DatabaseTables.GetAvailableTables(0, [], [], []),
                      ["key", "name", "import_type"], "GetAvailableTables")

    def table(self, key):
        values = self.call("GetTableForDisplayArray", self.model.DatabaseTables.GetTableForDisplayArray,
                           key, [], "All", 0, [], 0, [])
        requested, version, fields, n, data = values
        if n < 0 or (n and not fields) or len(data) != n*len(fields):
            raise EtabsError(f"Invalid design table layout: {key}")
        return [dict(zip(fields, data[i:i+len(fields)])) for i in range(0, len(data), len(fields))] if fields else []

    def combination_designs(self, frames, combinations, progress=None):
        """Isolate each strength combination and redesign, then restore all combinations.

        Summary envelopes cannot be relabeled as per-combination checks. Each result
        here comes from an independent ETABS StartDesign with one selected combination.
        """
        result = {}
        names = list(combinations)
        try:
            for index, selected in enumerate(names):
                for name in names:
                    self.call("Select design combination", self.model.DesignConcrete.SetComboStrength,
                              name, name == selected)
                if progress:
                    progress(index+1, len(names), selected)
                self.start_design()
                designs = {frame: self.design(frame) for frame in frames}
                for rows in designs.values():
                    for row in rows:
                        for key in ("top_combo", "bottom_combo", "shear_combo", "torsion_long_combo", "torsion_trans_combo"):
                            if row[key] and row[key] not in (selected, selected+" (Sp)"):
                                raise EtabsError(f"Per-combination design contains another combination: {row[key]}")
                result[selected] = dict(designs=designs,
                                        flexure=self.table("Concrete Beam Flexure Envelope - ACI 318-19"),
                                        shear=self.table("Concrete Beam Shear Envelope - ACI 318-19"))
        finally:
            for name in names:
                self.call("Restore design combinations", self.model.DesignConcrete.SetComboStrength, name, True)
            self.start_design()
        return result
