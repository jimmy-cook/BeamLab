"""Native Windows workflow for researchers; Python and COM details stay behind the UI."""
from __future__ import annotations

import csv
import json
import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import Config, Scenario, plan_rows, strength_combinations, candidate_rows, planned_model_count, search_groups
from .sampling import ResearchRanges, sample_study, factorial_counts, search_sections
from .pilot import load_excel_pilot
from .research import application_dir, new_run_folder, run_folder_name
from dataclasses import asdict
from .display import enable_dpi_awareness, initial_size, window_dpi
from .pipeline import run
from .serviceability import CATEGORIES, DURATIONS, ATTACHMENTS, TOTAL_BASES

PROTOCOLS = {"Research: 1.2D + 1.6L; all spans fully loaded": "research_full_udl",
             "Full UDL: also check 1.4D": "uniform_gravity", "Legacy: patterned live loads": "patterned_gravity"}
DEAD_BASES = {"D already includes beam self-weight": "total_including_self_weight",
              "D excludes beam self-weight; add it": "superimposed_plus_self_weight"}
DEFAULT_SCENARIO = dict(name="Three-span research sample", spans_m=[6, 6, 6], dead_kN_m=[20, 20, 20], live_kN_m=[10, 10, 10],
                        fc_MPa=30, fy_MPa=420, fyt_MPa=420, centroid_cover_mm=60,
                        concrete_weight_kN_m3=24, flexural_modifier=1.0)


class ScrollablePage(ttk.Frame):
    """Keep inputs/actions reachable on small screens and at large Windows scaling."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg="#f7f9fb")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas, padding=18)
        self.content = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self.resize)
        self.canvas.bind("<Configure>", self.resize)

    def resize(self, event=None):
        width = max(self.canvas.winfo_width(), self.body.winfo_reqwidth())
        height = max(self.canvas.winfo_height(), self.body.winfo_reqheight())
        self.canvas.itemconfigure(self.content, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def wheel(self, event):
        widget = event.widget
        while widget and widget is not self:
            if isinstance(widget, ttk.Treeview):
                return
            widget = getattr(widget, "master", None)
        if widget is self:
            units = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(units*3, "units")


class BeamLab(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        self.withdraw()
        self.title("ETABS Beam Lab 0.7 — ANN pilot dataset")
        self.dpi = window_dpi(self.winfo_id(), round(self.winfo_fpixels("1i")))
        self.tk.call("tk", "scaling", self.dpi/72)
        self.display_scale = self.dpi/96
        width, height = initial_size(self.winfo_screenwidth(), self.winfo_screenheight(), self.dpi)
        self.geometry(f"{width}x{height}")
        self.minsize(min(self.px(1000), width), min(self.px(660), height))
        self.configure(bg="#edf2f6")
        self.scenarios = json.loads(json.dumps([DEFAULT_SCENARIO]))
        self.sections = [[300., 600.]]
        self.protocol_choice = tk.StringVar(value=next(iter(PROTOCOLS)))
        self.dead_basis_choice = tk.StringVar(value=next(iter(DEAD_BASES)))
        self.range_limits = tk.BooleanVar(value=True)
        self.size_search = tk.BooleanVar(value=False)
        self.sampling_settings = asdict(ResearchRanges())
        self.width_depth_limits = (ResearchRanges.min_width_depth_ratio, ResearchRanges.max_width_depth_ratio)
        self.min_span_depth_ratio = ResearchRanges.min_span_depth_ratio
        self.messages = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.last_output = None
        self.selected_scenario = None
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Segoe UI", size=11)
        style = ttk.Style(self)
        self.style = style
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 11), background="#f7f9fb", foreground="#17334b")
        style.configure("TEntry", padding=(6, 5))
        style.configure("TNotebook", background="#edf2f6", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(20, 8), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
        style.configure("TButton", padding=(12, 6))
        style.configure("Accent.TButton", background="#087e8b", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#086a76"), ("disabled", "#9fb9bd")])
        style.configure("Treeview", rowheight=30, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        header = tk.Frame(self, bg="#102f46", height=92)
        header.pack(fill="x")
        tk.Label(header, text="ETABS Beam Lab", bg="#102f46", fg="white", font=("Segoe UI", 21, "bold")).pack(anchor="w", padx=25, pady=(12, 0))
        tk.Label(header, text="Continuous gravity beams  /  ACI 318-19  /  CSV datasets", bg="#102f46", fg="#b8dce5", font=("Segoe UI", 10)).pack(anchor="w", padx=26, pady=(2, 10))
        self.status = tk.StringVar(value="Ready. Start with the three-span sample, or enter your own study.")
        ttk.Label(self, text="Deflection PASS/FAIL follows your entered limits and selected basis. Complete reinforcement detailing remains separate.",
                  padding=(24, 4), foreground="#526879", font=("Segoe UI", 9)).pack(side="bottom", fill="x")
        ttk.Label(self, textvariable=self.status, padding=(24, 4), wraplength=1100).pack(side="bottom", fill="x")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=self.px(20), pady=(self.px(10), self.px(6)))
        self.pages = [ScrollablePage(self.notebook) for _ in range(4)]
        self.scenario_tab, self.section_tab, self.check_tab, self.export_tab = [page.body for page in self.pages]
        for tab, text in zip(self.pages,
                             ("1   Beam scenarios", "2   Candidate sizes", "3   Design checks", "4   Generate & export")):
            self.notebook.add(tab, text=text)
        self.make_scenarios()
        self.make_sections()
        self.make_checks()
        self.make_export()
        self.refresh_scenarios()
        self.refresh_sections()
        self.load_editor(0)
        self._tree_dimensions = {}
        for tree in (self.scenario_tree, self.section_tree, self.result_tree):
            self._tree_dimensions[tree] = {column: int(tree.column(column, "width")) for column in tree["columns"]}
        self.apply_display_metrics()
        self.bind_all("<MouseWheel>", self.scroll_page, add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.deiconify()
        self.after(1000, self.watch_dpi)
        self.after(200, self.poll)

    def px(self, value):
        return max(1, round(value*self.display_scale))

    def apply_display_metrics(self):
        self.style.configure("Treeview", rowheight=max(self.px(34), tkfont.Font(family="Segoe UI", size=11).metrics("linespace")+self.px(12)))
        self.style.configure("TEntry", padding=(self.px(6), self.px(5)))
        self.style.configure("TButton", padding=(self.px(14), self.px(8)))
        self.style.configure("TNotebook.Tab", padding=(self.px(20), self.px(10)))
        self.style.configure("Card.TLabelframe", padding=self.px(14))
        for tree, dimensions in self._tree_dimensions.items():
            for column, baseline in dimensions.items():
                tree.column(column, width=self.px(baseline), minwidth=self.px(min(baseline, 60)))
        for page in self.pages:
            page.body.configure(padding=self.px(18))
            page.after_idle(page.resize)

    def scroll_page(self, event):
        self.pages[self.notebook.index("current")].wheel(event)

    def watch_dpi(self):
        dpi = window_dpi(self.winfo_id(), self.dpi)
        if dpi != self.dpi:
            self.dpi = dpi
            self.display_scale = dpi/96
            self.tk.call("tk", "scaling", dpi/72)
            # Recreate font metrics after a monitor/DPI transition; glyphs stay native.
            for name in tkfont.names(self):
                font = tkfont.nametofont(name, root=self)
                font.configure(size=font.cget("size"))
            self.apply_display_metrics()
        self.after(1000, self.watch_dpi)

    def make_scenarios(self):
        tab = self.scenario_tab
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 12))
        ttk.Label(bar, text="Build one beam line per scenario. Add candidate sizes in step 2.", font=("Segoe UI", 11)).pack(side="left")
        ttk.Button(bar, text="Load study…", command=self.load_study).pack(side="right")
        ttk.Button(bar, text="Save study…", command=self.save_study).pack(side="right", padx=8)
        pilot = ttk.LabelFrame(tab, text="Ready-to-use ANN study", style="Card.TLabelframe")
        pilot.pack(fill="x", pady=(0, 12))
        ttk.Button(pilot, text="Load Excel pilot: 100 × 20", style="Accent.TButton",
                   command=self.load_pilot).pack(anchor="w")
        ttk.Label(pilot, text="The exact Excel plan: 2,000 candidate models / 6,000 beam rows.\n"
                  "Replaces the current study inputs. Review settings, then choose Generate CSV dataset to run ETABS.",
                  wraplength=1000, foreground="#526879").pack(anchor="w", pady=(8, 0))
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=310)
        left.pack(side="left", fill="both", padx=(0, 16))
        self.scenario_tree = ttk.Treeview(left, columns=("name", "spans"), show="headings", height=12, selectmode="browse")
        self.scenario_tree.heading("name", text="Scenario")
        self.scenario_tree.heading("spans", text="Spans")
        self.scenario_tree.column("name", width=215)
        self.scenario_tree.column("spans", width=55, anchor="center")
        self.scenario_tree.pack(fill="both", expand=True)
        self.scenario_tree.bind("<<TreeviewSelect>>", self.choose_scenario)
        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="New", command=self.new_scenario).pack(side="left")
        ttk.Button(actions, text="Remove", command=self.remove_scenario).pack(side="left", padx=5)
        ttk.Button(left, text="Sample many scenarios…", command=self.sample_dialog).pack(fill="x")
        right = ttk.LabelFrame(body, text="Scenario editor", style="Card.TLabelframe")
        right.pack(side="left", fill="both", expand=True)
        self.editor = {}
        fields = [("name", "Scenario name", "Three-span sample"), ("fc_MPa", "Concrete f′c (MPa)", "30"),
                  ("fy_MPa", "Longitudinal fy (MPa)", "420"), ("fyt_MPa", "Stirrups fyt (MPa)", "420"),
                  ("centroid_cover_mm", "Rebar centroid cover (mm)", "60"),
                  ("flexural_modifier", "Flexural stiffness modifier", "1.0")]
        for r, (key, label, value) in enumerate(fields):
            ttk.Label(right, text=label).grid(row=r, column=0, sticky="w", pady=2)
            variable = tk.StringVar(value=value)
            self.editor[key] = variable
            ttk.Entry(right, textvariable=variable, width=24).grid(row=r, column=1, columnspan=2, sticky="ew", pady=2)
        self.shared_dead, self.shared_live = tk.StringVar(value="20"), tk.StringVar(value="10")
        self.shared_entries = []
        for row, label, variable in ((6, "D on every span (kN/m)", self.shared_dead), (7, "L on every span (kN/m)", self.shared_live)):
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(right, textvariable=variable, width=20)
            entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
            self.shared_entries.append(entry)
        for col, name in enumerate(("Length (m)", "Dead D (kN/m)", "Live L (kN/m)"), 1):
            ttk.Label(right, text=name, font=("Segoe UI", 9, "bold")).grid(row=8, column=col, padx=3, sticky="w", pady=(16, 4))
        ttk.Label(right, text="Span", font=("Segoe UI", 9, "bold")).grid(row=8, column=0, sticky="w")
        self.span_vars = []
        self.span_load_entries = []
        for r in range(6):
            ttk.Label(right, text=f"{r+1}").grid(row=9+r, column=0, sticky="w", pady=1)
            variables = [tk.StringVar() for _ in range(3)]
            self.span_vars.append(variables)
            for col, var in enumerate(variables, 1):
                entry = ttk.Entry(right, textvariable=var, width=13)
                entry.grid(row=9+r, column=col, padx=3, sticky="ew", pady=1)
                if col > 1:
                    self.span_load_entries.append(entry)
            variables[0].trace_add("write", self.mirror_udl)
        self.shared_dead.trace_add("write", self.mirror_udl)
        self.shared_live.trace_add("write", self.mirror_udl)
        self.protocol_choice.trace_add("write", self.mirror_udl)
        ttk.Label(right, text="Research mode: one D and one L apply to all spans.\nRanges: span 3000–12000 mm; D,L 10–150 kN/m; fc 20–50 MPa; fy 200–600 MPa.",
                  foreground="#526879", font=("Segoe UI", 9)).grid(row=15, column=0, columnspan=4, sticky="w", pady=(8, 8))
        ttk.Button(right, text="Apply scenario", style="Accent.TButton", command=self.apply_scenario).grid(row=16, column=0, columnspan=4, sticky="ew")
        right.columnconfigure(2, weight=1)
        right.columnconfigure(3, weight=1)

    def make_sections(self):
        tab = self.section_tab
        ttk.Label(tab, text="Every scenario will be tested with each size listed below.", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        protocol = ttk.LabelFrame(tab, text="Research loading and ranges", style="Card.TLabelframe")
        protocol.pack(fill="x", pady=(0, 12))
        ttk.Combobox(protocol, textvariable=self.protocol_choice, values=list(PROTOCOLS), state="readonly", width=60).pack(fill="x", pady=5)
        ttk.Combobox(protocol, textvariable=self.dead_basis_choice, values=list(DEAD_BASES), state="readonly", width=60).pack(fill="x", pady=5)
        ttk.Checkbutton(protocol, text="Enforce research ranges: b 300–900 mm, h 350–1200 mm, and supplied load/material/span limits", variable=self.range_limits).pack(anchor="w", pady=5)
        ttk.Label(tab, text="One section is assigned to the whole continuous beam line. Failed designs remain in the CSV.").pack(anchor="w", pady=(0, 8))
        ttk.Checkbutton(tab, text="Smart size search: for each width below, find the smallest passing depth by halving (about 6 ETABS runs per width)\n"
                        "instead of running every size. Untick to run every listed size, e.g. to verify the search on a few beam lines.",
                        variable=self.size_search).pack(anchor="w", pady=(0, 20))
        self.section_tree = ttk.Treeview(tab, columns=("b", "h"), show="headings", height=8)
        self.section_tree.heading("b", text="Width b (mm)")
        self.section_tree.heading("h", text="Depth h (mm)")
        self.section_tree.pack(fill="x")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=15)
        self.width = tk.StringVar(value="300")
        self.depth = tk.StringVar(value="600")
        ttk.Label(row, text="Width (mm)").pack(side="left")
        ttk.Entry(row, textvariable=self.width, width=10).pack(side="left", padx=(8, 20))
        ttk.Label(row, text="Depth (mm)").pack(side="left")
        ttk.Entry(row, textvariable=self.depth, width=10).pack(side="left", padx=8)
        ttk.Button(row, text="Add size", command=self.add_section).pack(side="left", padx=8)
        ttk.Button(row, text="Remove selected", command=self.remove_sections).pack(side="left")
        ttk.Button(row, text="Six-size study preset", command=self.preset_sections).pack(side="right")
        advanced = ttk.LabelFrame(tab, text="Collection settings", style="Card.TLabelframe")
        advanced.pack(fill="x", pady=12)
        self.stations = tk.StringVar(value="21")
        self.exe = tk.StringVar(value=r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe")
        ttk.Label(advanced, text="Output stations per span (odd number)").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(advanced, textvariable=self.stations, width=10).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Label(advanced, text="ETABS 22 application").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(advanced, textvariable=self.exe, width=65).grid(row=1, column=1, sticky="ew", padx=12)
        ttk.Button(advanced, text="Browse…", command=self.browse_exe).grid(row=1, column=2)
        advanced.columnconfigure(1, weight=1)
        ttk.Label(tab, text="Research preset: Wu = 1.2D + 1.6L; all spans fully loaded. Beam self-weight follows the dead-load choice above.\nSystem: ideal continuous gravity beam supports; ACI 318-19 non-sway design.",
                  foreground="#526879").pack(anchor="w", pady=12)

    def make_export(self):
        tab = self.export_tab
        location = ttk.LabelFrame(tab, text="CSV output folder", style="Card.TLabelframe")
        location.pack(fill="x")
        self.out_dir = tk.StringVar(value=str(application_dir()/"runs"))
        ttk.Entry(location, textvariable=self.out_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(location, text="Choose…", command=self.choose_output).pack(side="left", padx=(10, 0))
        named = ttk.Frame(tab)
        named.pack(fill="x", pady=(10, 0))
        self.run_name = tk.StringVar()
        ttk.Label(named, text="Run name (optional)").pack(side="left")
        ttk.Entry(named, textvariable=self.run_name, width=28).pack(side="left", padx=10)
        ttk.Label(named, text="e.g. Phase1 → folder \"Phase1 EqualSpan UniformLoad 04Nov 0930\"; empty uses the seed",
                  foreground="#526879").pack(side="left")
        ttk.Label(tab, text="A new run folder is created automatically. Existing CSVs are preserved.", foreground="#526879").pack(anchor="w", pady=8)
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=12)
        self.review_button = ttk.Button(actions, text="Review study", command=self.preview)
        self.review_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="Generate CSV dataset", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left", padx=10)
        self.stop_button = ttk.Button(actions, text="Stop after current model", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.summary = tk.StringVar(value="Review the study to see its model and beam counts.")
        ttk.Label(tab, textvariable=self.summary, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 8))
        self.progress = ttk.Progressbar(tab, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 14))
        self.result_tree = ttk.Treeview(tab, columns=("scenario", "beam", "section", "moment", "shear", "torsion", "sls"), show="headings", height=5)
        for key, text, width in (("scenario", "Scenario", 230), ("beam", "Beam", 65), ("section", "b × h (mm)", 110),
                                 ("moment", "Section: moment", 145), ("shear", "Section: shear", 135),
                                 ("torsion", "Section: torsion", 140), ("sls", "Deflection", 125)):
            self.result_tree.heading(key, text=text)
            self.result_tree.column(key, width=width)
        self.result_tree.pack(fill="both", expand=True)
        self.result_tree.tag_configure("errors", foreground="#a93a35")
        self.result_tree.tag_configure("summary_clear", foreground="#147263")
        self.result_tree.tag_configure("FAIL", foreground="#b2322b")
        self.result_tree.tag_configure("REVIEW", foreground="#9c6809")
        self.result_tree.tag_configure("PASS", foreground="#147263")
        self.result_tree.bind("<<TreeviewSelect>>", self.result_detail)
        self.detail = tk.StringVar(value="Select a result to see its ETABS design message.")
        ttk.Label(tab, textvariable=self.detail, wraplength=1000, foreground="#526879").pack(fill="x", pady=8)
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(6, 0))
        self.open_buttons = []
        for label, name in (("Open CSV folder", None), ("Beam dataset", "beams.csv"),
                            ("ANN beam CSV", "ann_beams.csv"), ("Size ANN CSV", "ann_size.csv"),
                            ("Size ANN, one row per beam", "ann_size_wide.csv"), ("Deflection ANN CSV", "ann_deflection.csv")):
            button = ttk.Button(buttons, text=label, command=lambda n=name: self.open_result(n), state="disabled")
            button.pack(side="left", padx=(0, 8))
            self.open_buttons.append(button)
        ttk.Button(buttons, text="Checks for selected beam…", command=self.show_checks).pack(side="left", padx=(0, 8))

    def make_checks(self):
        tab = self.check_tab
        self.case_checks = tk.BooleanVar(value=True)
        ttk.Label(tab, text="Automatic reinforcement and deflection dataset", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(tab, text="ETABS exports required top/bottom steel (mm²) and shear reinforcement (mm²/m) automatically.\nNo manually provided reinforcement is entered. The deflection calculation uses those idealized required areas.", wraplength=1000, foreground="#526879").pack(anchor="w", pady=(0, 18))
        settings = ttk.LabelFrame(tab, text="Deflection Limits", style="Card.TLabelframe")
        settings.pack(fill="x", pady=8)
        self.sls_enabled = tk.BooleanVar(value=True)
        self.sls_legacy = tk.BooleanVar(value=False)
        self.sls_advanced_open = tk.BooleanVar(value=False)
        self.sls_category = tk.StringVar()
        self.sls_fraction = tk.StringVar()
        self.sls_duration = tk.StringVar()
        self.sls_attachment = tk.StringVar()
        self.sls_tolerance = tk.StringVar()
        self.sls_dead_limit = tk.StringVar(value="120")
        self.sls_total_limit = tk.StringVar(value="240")
        self.sls_total_basis = tk.StringVar(value=next(iter(TOTAL_BASES)))
        ttk.Checkbutton(settings, text="Include deflection checks and ANN CSV output", variable=self.sls_enabled).grid(row=0, column=0, columnspan=2, sticky="w", pady=8)
        for row,label,variable in ((1,"DL Deflection Limit, L/",self.sls_dead_limit),(2,"Total Deflection Limit, L/",self.sls_total_limit)):
            ttk.Label(settings, text=label, font=("Segoe UI",11)).grid(row=row, column=0, sticky="w", pady=10)
            ttk.Entry(settings, textvariable=variable, width=18, justify="right").grid(row=row, column=1, sticky="w", padx=18, pady=6)
        ttk.Label(settings, text="Total deflection basis").grid(row=3, column=0, sticky="w", pady=10)
        ttk.Combobox(settings, textvariable=self.sls_total_basis, values=list(TOTAL_BASES), state="readonly", width=53).grid(row=3, column=1, sticky="ew", padx=18)
        self.sls_explanation = tk.StringVar()
        ttk.Label(settings, textvariable=self.sls_explanation, wraplength=1000, foreground="#526879").grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        self.sls_advanced_button = ttk.Button(settings, text="Advanced settings…", command=self.toggle_serviceability_advanced)
        self.sls_advanced_button.grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        self.sls_advanced = ttk.LabelFrame(settings, text="Long-term assumptions / legacy ACI checks", style="Card.TLabelframe")
        self.sls_advanced.grid(row=6, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(self.sls_advanced, text="Sustained live load (%)").grid(row=0,column=0,sticky="w",pady=6)
        ttk.Entry(self.sls_advanced,textvariable=self.sls_fraction,width=18).grid(row=0,column=1,sticky="w",padx=18)
        ttk.Label(self.sls_advanced,text="Sustained load duration").grid(row=1,column=0,sticky="w",pady=6)
        ttk.Combobox(self.sls_advanced,textvariable=self.sls_duration,values=list(DURATIONS),state="readonly",width=25).grid(row=1,column=1,sticky="w",padx=18)
        ttk.Label(self.sls_advanced,text="Required for long-term total: enter the actual sustained live percentage and choose a duration.\nTotal long-term = immediate D+L plus additional creep/shrinkage deflection. DL always checks immediate D.",wraplength=950,foreground="#526879").grid(row=2,column=0,columnspan=2,sticky="w",pady=10)
        ttk.Checkbutton(self.sls_advanced,text="Use ACI-category checks (legacy / advanced)",variable=self.sls_legacy).grid(row=3,column=0,columnspan=2,sticky="w",pady=10)
        self.sls_aci_frame = ttk.Frame(self.sls_advanced)
        self.sls_aci_frame.grid(row=4,column=0,columnspan=2,sticky="ew")
        for row,label,variable,choices in ((0,"What does this beam support?",self.sls_category,CATEGORIES),
                                          (1,"When are walls / finishes attached?",self.sls_attachment,ATTACHMENTS)):
            ttk.Label(self.sls_aci_frame,text=label).grid(row=row,column=0,sticky="w",pady=6)
            ttk.Combobox(self.sls_aci_frame,textvariable=variable,values=list(choices),state="readonly",width=55).grid(row=row,column=1,sticky="ew",padx=18)
        ttk.Label(self.sls_aci_frame,text="Non-sensitive attachment tolerance (mm)").grid(row=2,column=0,sticky="w",pady=6)
        ttk.Entry(self.sls_aci_frame,textvariable=self.sls_tolerance,width=18).grid(row=2,column=1,sticky="w",padx=18)
        ttk.Label(self.sls_aci_frame,text="ACI-category mode adds its applicable live-load / after-attachment criterion. The entered DL and total limits remain separate project criteria.",wraplength=950,foreground="#526879").grid(row=3,column=0,columnspan=2,sticky="w",pady=10)
        self.sls_aci_frame.columnconfigure(1,weight=1)
        self.sls_advanced.columnconfigure(1,weight=1)
        settings.columnconfigure(1,weight=1)
        ttk.Label(tab,text="RC method: ETABS service moments + approximate ACI midspan cracked stiffness.\nLimits are user-defined in simple mode; passing them is not a complete ACI acceptance label.\nA new ann_deflection.csv records the limits, total basis, deflections and separate DL/Total/Overall labels.",wraplength=1000,foreground="#526879").pack(anchor="w",pady=14)
        self.sls_total_basis.trace_add("write",self.update_serviceability_panels)
        self.sls_legacy.trace_add("write",self.update_serviceability_panels)
        self.update_serviceability_panels()

    def toggle_serviceability_advanced(self):
        self.sls_advanced_open.set(not self.sls_advanced_open.get())
        self.update_serviceability_panels()

    def update_serviceability_panels(self,*args):
        long_term = TOTAL_BASES.get(self.sls_total_basis.get()) == "long_term"
        if self.sls_advanced_open.get() or long_term or self.sls_legacy.get():
            self.sls_advanced.grid()
        else:
            self.sls_advanced.grid_remove()
        if self.sls_legacy.get():
            self.sls_aci_frame.grid()
            self.sls_explanation.set("Legacy ACI-category checks are enabled, with any entered project limits also evaluated.\nClear optional project limits if you want only the applicable ACI-category criterion.")
        else:
            self.sls_aci_frame.grid_remove()
            self.sls_explanation.set("User-defined limits: DL is immediate dead-load deflection; Total uses the selected basis.\nAllowable deflection = span / entered divisor. PASS means calculated deflection does not exceed that limit.\n120 and 240 are editable starting values, not universal ACI RC limits.")
        self.sls_advanced_button.configure(text="Hide advanced settings" if self.sls_advanced_open.get() else "Advanced settings…")
        self.pages[2].after_idle(self.pages[2].resize)

    def serviceability_from_form(self):
        def optional(variable):
            return float(variable.get()) if variable.get().strip() else None
        legacy = self.sls_legacy.get()
        total_basis = TOTAL_BASES[self.sls_total_basis.get()]
        needs_time = legacy or total_basis == "long_term"
        fraction = optional(self.sls_fraction) if needs_time else None
        return dict(enabled=self.sls_enabled.get(), limit_mode="aci" if legacy else "user_defined", total_basis=total_basis,
                    category=CATEGORIES.get(self.sls_category.get()) if legacy else None,
                    sustained_live_fraction=fraction/100 if fraction is not None else None,
                    duration_months=DURATIONS.get(self.sls_duration.get()) if needs_time else None,
                    attachment_timing=ATTACHMENTS.get(self.sls_attachment.get()) if legacy else None,
                    attachment_tolerance_mm=optional(self.sls_tolerance) if legacy else None,
                    dead_limit_divisor=optional(self.sls_dead_limit), total_limit_divisor=optional(self.sls_total_limit))

    def load_serviceability(self, settings):
        self.sls_enabled.set(settings.enabled)
        self.sls_legacy.set(settings.limit_mode == "aci")
        self.sls_total_basis.set(next(k for k,v in TOTAL_BASES.items() if v == settings.total_basis))
        self.sls_category.set(next((k for k,v in CATEGORIES.items() if v == settings.category), ""))
        self.sls_duration.set(next((k for k,v in DURATIONS.items() if v == settings.duration_months), ""))
        self.sls_attachment.set(next((k for k,v in ATTACHMENTS.items() if v == settings.attachment_timing), ""))
        self.sls_tolerance.set("" if settings.attachment_tolerance_mm is None else f"{settings.attachment_tolerance_mm:g}")
        self.sls_fraction.set("" if settings.sustained_live_fraction is None else f"{100*settings.sustained_live_fraction:g}")
        self.sls_dead_limit.set("" if settings.dead_limit_divisor is None else f"{settings.dead_limit_divisor:g}")
        self.sls_total_limit.set("" if settings.total_limit_divisor is None else f"{settings.total_limit_divisor:g}")
        self.sls_advanced_open.set(settings.limit_mode == "aci" or settings.total_basis == "long_term")
        self.update_serviceability_panels()

    def refresh_scenarios(self):
        self.scenario_tree.delete(*self.scenario_tree.get_children())
        for index, scenario in enumerate(self.scenarios):
            self.scenario_tree.insert("", "end", iid=str(index), values=(scenario["name"], len(scenario["spans_m"])))

    def refresh_sections(self):
        self.section_tree.delete(*self.section_tree.get_children())
        for index, (b, h) in enumerate(self.sections):
            self.section_tree.insert("", "end", iid=str(index), values=(f"{b:g}", f"{h:g}"))

    def load_editor(self, index):
        scenario = self.scenarios[index]
        self.selected_scenario = index
        for key, variable in self.editor.items():
            variable.set(scenario.get(key, DEFAULT_SCENARIO[key]))
        for i, variables in enumerate(self.span_vars):
            for key, variable in zip(("spans_m", "dead_kN_m", "live_kN_m"), variables):
                variable.set(f"{scenario[key][i]:g}" if i < len(scenario["spans_m"]) else "")
        self.shared_dead.set(f"{scenario['dead_kN_m'][0]:g}")
        self.shared_live.set(f"{scenario['live_kN_m'][0]:g}")
        self.mirror_udl()

    def mirror_udl(self, *args):
        uniform = PROTOCOLS[self.protocol_choice.get()] != "patterned_gravity"
        for entry in getattr(self, "span_load_entries", []):
            entry.configure(state="readonly" if uniform else "normal")
        for entry in getattr(self, "shared_entries", []):
            entry.configure(state="normal" if uniform else "disabled")
        if uniform:
            for length, dead, live in getattr(self, "span_vars", []):
                dead.set(self.shared_dead.get() if length.get().strip() else "")
                live.set(self.shared_live.get() if length.get().strip() else "")

    def choose_scenario(self, event=None):
        selected = self.scenario_tree.selection()
        if selected:
            self.load_editor(int(selected[0]))

    def new_scenario(self):
        self.selected_scenario = None
        self.scenario_tree.selection_remove(*self.scenario_tree.selection())
        self.editor["name"].set(f"Scenario {len(self.scenarios)+1}")
        self.status.set("Enter the new beam line, then choose Apply scenario.")

    def editor_scenario(self):
        scenario = {key: (var.get().strip() if key == "name" else float(var.get())) for key, var in self.editor.items()}
        scenario.update({"spans_m": [], "dead_kN_m": [], "live_kN_m": []})
        blank_seen = False
        for variables in self.span_vars:
            values = [v.get().strip() for v in variables]
            if not any(values):
                blank_seen = True
                continue
            if blank_seen or not all(values):
                raise ValueError("Fill consecutive span rows with length, dead load and live load. Leave all unused cells blank.")
            for key, value in zip(("spans_m", "dead_kN_m", "live_kN_m"), values):
                scenario[key].append(float(value))
        old = self.scenarios[self.selected_scenario] if self.selected_scenario is not None else DEFAULT_SCENARIO
        scenario["concrete_weight_kN_m3"] = old.get("concrete_weight_kN_m3", 24)
        # Keep unchanged saved numbers (60 versus 60.0) identical for canonical IDs.
        # Merely viewing/reviewing the Excel pilot must not create new scenarios.
        for key, value in old.items():
            if key in scenario and scenario[key] == value:
                scenario[key] = value
        Scenario.parse(scenario)
        return scenario

    def apply_scenario(self):
        if self.busy:
            return
        try:
            scenario = self.editor_scenario()
            scenarios = list(self.scenarios)
            if self.selected_scenario is None:
                scenarios.append(scenario)
                index = len(scenarios)-1
            else:
                index = self.selected_scenario
                scenarios[index] = scenario
            self.config_for(scenarios=scenarios)
            self.scenarios = scenarios
            self.refresh_scenarios()
            self.scenario_tree.selection_set(str(index))
            self.status.set(f"Saved {scenario['name']}. Move to Candidate sizes when ready.")
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check scenario", str(exc), parent=self)

    def remove_scenario(self):
        if self.busy:
            return
        selected = self.scenario_tree.selection()
        if not selected or len(self.scenarios) <= 1:
            return
        self.scenarios.pop(int(selected[0]))
        self.refresh_scenarios()
        self.load_editor(0)

    def config_for(self, scenarios=None, sections=None):
        scenarios = self.scenarios if scenarios is None else scenarios
        sections = self.sections if sections is None else sections
        return Config.parse(dict(scenarios=scenarios, sections_mm=sections, stations_per_span=int(self.stations.get()),
                                 max_models=max(1, planned_model_count(len(scenarios), sections, self.size_search.get())),
                                 etabs_executable=self.exe.get().strip(), size_search=self.size_search.get(),
                                 check_combinations=True,
                                 serviceability=self.serviceability_from_form(),
                                 loading_protocol=PROTOCOLS[self.protocol_choice.get()], dead_load_basis=DEAD_BASES[self.dead_basis_choice.get()],
                                 enforce_research_ranges=self.range_limits.get(), sampling_settings=self.sampling_settings,
                                 min_width_depth_ratio=self.width_depth_limits[0], max_width_depth_ratio=self.width_depth_limits[1],
                                 min_span_depth_ratio=self.min_span_depth_ratio))

    def add_section(self):
        if self.busy:
            return
        try:
            sections = self.sections + [[float(self.width.get()), float(self.depth.get())]]
            self.config_for(sections=sections)
            self.sections = sections
            self.refresh_sections()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check candidate size", str(exc), parent=self)

    def remove_sections(self):
        if self.busy:
            return
        indices = {int(i) for i in self.section_tree.selection()}
        sections = [s for i, s in enumerate(self.sections) if i not in indices]
        if sections:
            self.sections = sections
            self.refresh_sections()

    def preset_sections(self):
        if not self.busy:
            self.sections = [[300, 350], [300, 600], [450, 750], [600, 900], [750, 1050], [900, 1200]]
            self.refresh_sections()

    def browse_exe(self):
        path = filedialog.askopenfilename(title="Select ETABS 22", filetypes=[("ETABS", "ETABS.exe")])
        if path:
            self.exe.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(title="Choose parent folder for CSV runs")
        if path:
            self.out_dir.set(path)

    def save_study(self):
        try:
            config = self.sync_editor()
            path = filedialog.asksaveasfilename(title="Save study", defaultextension=".json", filetypes=[("Beam study", "*.json")])
            if path:
                Path(path).write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
                self.status.set("Study saved. You can reopen it without re-entering inputs.")
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("Cannot save study", str(exc), parent=self)

    def load_study(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="Open study", filetypes=[("Beam study", "*.json")])
        if not path:
            return
        try:
            config = Config.load(path)
            self.apply_study(config)
            self.status.set(f"Loaded {len(self.scenarios)} scenarios.")
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("Cannot open study", str(exc), parent=self)

    def apply_study(self, config):
        self.scenarios = config.to_dict()["scenarios"]
        self.sections = [list(section) for section in config.sections_mm]
        self.stations.set(config.stations_per_span)
        self.exe.set(config.etabs_executable)
        self.protocol_choice.set(next(k for k, v in PROTOCOLS.items() if v == config.loading_protocol))
        self.dead_basis_choice.set(next(k for k, v in DEAD_BASES.items() if v == config.dead_load_basis))
        self.range_limits.set(config.enforce_research_ranges)
        self.sampling_settings = config.sampling_settings or asdict(ResearchRanges())
        self.width_depth_limits = (config.min_width_depth_ratio, config.max_width_depth_ratio)
        self.min_span_depth_ratio = config.min_span_depth_ratio
        self.size_search.set(config.size_search)
        self.case_checks.set(True)
        self.load_serviceability(config.serviceability)
        self.refresh_scenarios()
        self.refresh_sections()
        self.load_editor(0)

    def load_pilot(self):
        if self.busy:
            return
        try:
            # Preserve a locally chosen ETABS installation on the receiving PC.
            executable = self.exe.get()
            self.apply_study(load_excel_pilot())
            self.exe.set(executable)
            if self.preview():
                self.notebook.select(self.pages[3])
                self.status.set("Excel pilot loaded. 1,962 models eligible for analysis; 38 invalid inputs retained. "
                                "Review Candidate sizes and Design checks, then Generate CSV dataset when ready.")
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("Cannot load Excel pilot", str(exc), parent=self)

    def preview(self):
        try:
            config = self.sync_editor()
            config.serviceability.require_ready()
            if config.size_search:
                return self.preview_search(config)
            total = len(config.scenarios)*len(config.sections_mm)
            spans = sum(len(s.spans_m) for s in config.scenarios)*len(config.sections_mm)
            combinations = sum(len(strength_combinations(len(s.spans_m), config.loading_protocol)) for s in config.scenarios)*len(config.sections_mm) if config.check_combinations else 0
            invalid = len({r["candidate_id"] for r in plan_rows(config) if not r["input_valid"]})
            deep = len({r["candidate_id"] for r in plan_rows(config) if "Deep beam" in r["input_validation_error"]})
            self.summary.set(f"{len(config.scenarios)} scenarios  •  {total:,} candidate models  •  {spans:,} planned beam rows\n"
                             f"{total-invalid:,} models eligible for analysis  •  {invalid} invalid inputs skipped ({deep} deep beams)  •  {combinations:,} combination checks planned")
            self.result_tree.delete(*self.result_tree.get_children())
            for row in plan_rows(config):
                planned = "Planned" if row["input_valid"] else "Deep beam" if "Deep beam" in row["input_validation_error"] else "Invalid input"
                self.result_tree.insert("", "end", values=(row["scenario_name"], row["beam_id"],
                                                           f"{row['b_mm']:g} × {row['h_mm']:g}", planned, planned, planned,
                                                           planned if config.serviceability.enabled else "Disabled"))
            return config
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check study settings", str(exc), parent=self)

    def preview_search(self, config):
        groups = config.search_groups()
        sls = config.serviceability.enabled
        skipped = 0
        self.result_tree.delete(*self.result_tree.get_children())
        for scenario in config.scenarios:
            for b, depths in groups:
                valid = [h for h in depths if all(r["input_valid"] for r in candidate_rows(config, scenario, b, h, ""))]
                skipped += len(depths)-len(valid)
                text = f"b {b:g}: h {valid[0]:g}–{valid[-1]:g} ({len(valid)})" if valid else f"b {b:g}: no valid depth"
                self.result_tree.insert("", "end", values=(scenario.name, "All spans", text, "Search", "Search", "Search",
                                                           "Search" if sls else "Disabled"))
        planned = config.planned_models()
        self.summary.set(f"{len(config.scenarios)} scenarios  •  smart size search over {len(groups)} widths ({len(config.sections_mm)} grid sizes)  •  "
                         f"at most {planned:,} ETABS models ({planned//len(config.scenarios)} per beam line)\n"
                         f"{skipped:,} scenario sizes skipped before ETABS (deep beam or self-weight)  •  "
                         + ("pass rule: strength and deflection" if sls else
                            "pass rule: strength only. Enable deflection checks so the search matches ann_size.csv"))
        return config

    def sync_editor(self):
        """Use the current form values when saving or running, even without Apply."""
        scenario = self.editor_scenario()
        scenarios = list(self.scenarios)
        index = self.selected_scenario
        if index is None:
            scenarios.append(scenario)
            index = len(scenarios)-1
        else:
            scenarios[index] = scenario
        config = self.config_for(scenarios=scenarios)
        self.scenarios = config.to_dict()["scenarios"]
        self.refresh_scenarios()
        self.selected_scenario = index
        return config

    def start(self):
        if self.busy:
            return
        config = self.preview()
        if not config:
            return
        if not Path(config.etabs_executable).is_file():
            messagebox.showerror("ETABS not found", "Choose your ETABS 22 application on the Candidate sizes tab.", parent=self)
            return
        output = new_run_folder(Path(self.out_dir.get().strip()).expanduser(), config, self.run_name.get())
        try:
            output.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            messagebox.showerror("Cannot create output folder", str(exc), parent=self)
            return
        self.last_output = output
        self.busy = True
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.review_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        # Freeze study inputs while the worker owns the current configuration.
        self.notebook.tab(self.pages[0], state="disabled")
        self.notebook.tab(self.pages[1], state="disabled")
        self.notebook.tab(self.pages[2], state="disabled")
        for button in self.open_buttons:
            button.configure(state="disabled")
        self.progress.configure(maximum=config.planned_models(), value=0)
        self.status.set("Starting ETABS. The first launch may take a moment…")
        def work():
            try:
                manifest = run(config, output, lambda item: self.messages.put(("progress", item)),
                               stop_requested=self.stop_event.is_set)
                self.messages.put(("done", manifest))
            except Exception as exc:
                self.messages.put(("done", {"state": "failed", "issues": [str(exc)]}))
        threading.Thread(target=work, daemon=False, name="BeamLab-COM").start()

    def stop(self):
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Stopping after the current model finishes. Completed CSV data will be kept.")

    def poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == "progress":
                    self.progress.configure(value=data["completed_candidates"])
                    self.status.set(f"{data['completed_candidates']}/{data['total_candidates']} models  ·  {data.get('current_stage', 'Starting ETABS')}  ·  {data.get('current_candidate', '')}")
                else:
                    self.finished(data)
        except queue.Empty:
            pass
        self.after(200, self.poll)

    def finished(self, manifest):
        self.busy = False
        self.start_button.configure(state="normal")
        self.review_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.notebook.tab(self.pages[0], state="normal")
        self.notebook.tab(self.pages[1], state="normal")
        self.notebook.tab(self.pages[2], state="normal")
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_rows = {}
        path = self.last_output/"beams.csv"
        rows = []
        if path.is_file():
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        for index, row in enumerate(rows):
            self.result_rows[str(index)] = row
            self.result_tree.insert("", "end", iid=str(index), values=(row["scenario_name"], row["beam_id"],
                                   f"{float(row['b_mm']):g} × {float(row['h_mm']):g}", row.get("section_moment_status", "NOT_EVALUATED"),
                                   row.get("section_shear_status", "NOT_EVALUATED"), row.get("section_torsion_status", "NOT_EVALUATED"), row.get("sls_overall_status", "NOT_EVALUATED")),
                                   tags=("FAIL" if "FAIL" in (row.get("section_overall_status"), row.get("sls_overall_status")) else
                                         "PASS" if row.get("section_overall_status") == "PASS" and row.get("sls_overall_status") == "PASS" else "REVIEW",))
        clear = sum(row["etabs_summary_clear"] == "1" for row in rows)
        self.summary.set(f"{len(rows)} beam rows  •  Strength PASS: {sum(r.get('section_overall_status') == 'PASS' for r in rows)}  •  Deflection PASS: {sum(r.get('sls_overall_status') == 'PASS' for r in rows)}  •  Deflection FAIL: {sum(r.get('sls_overall_status') == 'FAIL' for r in rows)}")
        self.status.set(f"Run {manifest['state'].replace('_', ' ')}. CSV folder: {self.last_output}")
        self.detail.set(" | ".join(manifest.get("issues", [])) or "Select a row for its design message. Open the CSV folder to view every exported table.")
        for button in self.open_buttons:
            button.configure(state="normal")

    def result_detail(self, event=None):
        selected = self.result_tree.selection()
        if selected and selected[0] in getattr(self, "result_rows", {}):
            row = self.result_rows[selected[0]]
            self.detail.set(row.get("runtime_error") or row.get("design_errors") or row.get("design_warnings") or
                            row.get("sls_reason") or f"ETABS section: {row.get('section_overall_status')}. Deflection ({row.get('sls_limit_source', 'disabled')}): {row.get('sls_overall_status')}. Complete detailing remains separate.")

    def show_checks(self):
        selected = self.result_tree.selection()
        if not selected or selected[0] not in getattr(self, "result_rows", {}):
            messagebox.showinfo("Select a beam", "Select a completed beam row first.", parent=self)
            return
        base = self.result_rows[selected[0]]
        dialog = tk.Toplevel(self)
        dialog.title(f"{base['beam_id']} — ETABS required reinforcement and checks")
        dialog.geometry(f"{self.px(1100)}x{self.px(600)}")
        book = ttk.Notebook(dialog)
        book.pack(fill="both", expand=True, padx=12, pady=12)
        specs = (("Section feasibility", "combination_checks.csv", ("combination", "section_moment_status", "section_shear_status", "section_torsion_status", "section_overall_status", "section_error")),
                 ("Deflection checks", "deflection_checks.csv", ("check", "basis", "deflection_mm", "limit_mm", "utilization", "status", "governing_station_mm")),
                 ("ETABS-required reinforcement", "combination_design_stations.csv", ("combination", "station_mm", "As_top_mm2", "As_bottom_mm2", "Av_over_s_mm2_per_m", "error", "warning")))
        for title, filename, fields in specs:
            page = ScrollablePage(book)
            book.add(page, text=title)
            tree = ttk.Treeview(page.body, columns=fields, show="headings", height=12)
            for key in fields:
                tree.heading(key, text=key.replace("_", " "))
                tree.column(key, width=self.px(240 if key == "combination" else 180))
            path = self.last_output/filename
            if path.is_file():
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    for row in csv.DictReader(stream):
                        if row["candidate_id"] == base["candidate_id"] and row["beam_id"] == base["beam_id"]:
                            tree.insert("", "end", values=tuple(row.get(key, "") for key in fields))
            tree.pack(fill="both", expand=True)
        ttk.Label(dialog, text="All reinforcement shown is required reinforcement calculated by ETABS. Asv is Av/s in mm²/m. Section feasibility is separate from final detailing and serviceability acceptance.", wraplength=self.px(1000)).pack(fill="x", padx=16, pady=12)

    def open_result(self, filename):
        path = self.last_output/filename if filename else self.last_output
        if path and path.exists():
            os.startfile(str(path.resolve()))

    def sample_dialog(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Research parameter ranges")
        dialog.transient(self)
        width, height = initial_size(self.winfo_screenwidth(), self.winfo_screenheight(), self.dpi)
        dialog.geometry(f"{min(width, self.px(850))}x{min(height, self.px(780))}")
        page = ScrollablePage(dialog)
        page.pack(fill="both", expand=True)
        frame = page.body
        ttk.Label(frame, text="Create the research input study", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, columnspan=3, pady=(0, 14), sticky="w")
        defaults = asdict(ResearchRanges())
        defaults.update({k: v for k, v in self.sampling_settings.items() if k in defaults})
        variables = {}
        for r, (key, label) in enumerate((("count", "Number of scenarios"), ("seed", "Random seed"),
                                         ("candidate_count", "Shared candidate sizes per scenario"), ("section_step_mm", "Section size grid step (mm)"),
                                         ("span_step_mm", "Span length grid step (mm)"), ("max_adjacent_span_ratio", "Max neighbouring span ratio (0 = no limit)"),
                                         ("min_width_depth_ratio", "Minimum width ÷ depth, b/h (0 = none)"), ("max_width_depth_ratio", "Maximum width ÷ depth, b/h (0 = none)"),
                                         ("min_span_depth_ratio", "Span must exceed this × h (4 = no ACI deep beams, 0 = off)")), 1):
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", pady=4)
            variables[key] = tk.StringVar(value=str(defaults[key]))
            ttk.Entry(frame, textvariable=variables[key], width=14).grid(row=r, column=1, padx=20, pady=4)
        ttk.Label(frame, text="Minimum", font=("Segoe UI", 10, "bold")).grid(row=10, column=1)
        ttk.Label(frame, text="Maximum", font=("Segoe UI", 10, "bold")).grid(row=10, column=2)
        ranges = (("Number of spans", "spans_min", "spans_max"), ("Span length (mm)", "span_min_mm", "span_max_mm"),
                  ("Dead D, including self-weight (kN/m)", "dead_min", "dead_max"),
                  ("Live L (kN/m)", "live_min", "live_max"), ("Live/dead ratio L/D (0 = no limit)", "min_live_dead_ratio", "max_live_dead_ratio"),
                  ("Concrete fc (MPa)", "fc_min", "fc_max"),
                  ("Rebar fy (MPa)", "fy_min", "fy_max"), ("Stirrup fyt (MPa, ACI shear limit 420)", "fyt_min", "fyt_max"),
                  ("Beam width b (mm)", "width_min_mm", "width_max_mm"),
                  ("Beam depth h (mm)", "depth_min_mm", "depth_max_mm"))
        for row, (label, minimum, maximum) in enumerate(ranges, 11):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            for col, key in ((1, minimum), (2, maximum)):
                variables[key] = tk.StringVar(value=str(defaults[key]))
                ttk.Entry(frame, textvariable=variables[key], width=14).grid(row=row, column=col, padx=20, pady=5)
        match_fy = tk.BooleanVar(value=defaults["stirrup_matches_fy"])
        equal_spans = tk.BooleanVar(value=defaults["equal_spans"])
        whole_units = tk.BooleanVar(value=defaults["whole_unit_parameters"])
        replace_current = tk.BooleanVar(value=True)
        size_search = tk.BooleanVar(value=defaults["size_search"])
        search_widths = tk.StringVar(value=", ".join(f"{w:g}" for w in defaults["search_widths_mm"]))
        dead_total = tk.BooleanVar(value=defaults["dead_includes_self_weight"])
        ttk.Checkbutton(frame, text="All spans in a beam line have the same length", variable=equal_spans).grid(row=21, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Optional: restrict D/L/fc/fy to whole numbers (off for range sampling)", variable=whole_units).grid(row=22, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Stirrups copy the main-bar fy, capped at 420 MPa (untick: stirrup fyt from its own range)", variable=match_fy).grid(row=23, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Replace current study with this sampled study", variable=replace_current).grid(row=24, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="Smart size search: find the smallest passing depth for each width below (no random candidate sizes)",
                        variable=size_search).grid(row=25, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(frame, text="Search widths b (mm, comma separated)").grid(row=26, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=search_widths, width=30).grid(row=26, column=1, columnspan=2, sticky="w", padx=20, pady=4)
        ttk.Checkbutton(frame, text="D already includes beam self-weight (ETABS self-weight off). Untick: D is superimposed and ETABS adds self-weight",
                        variable=dead_total).grid(row=27, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(frame, text="Default: 3 equal spans; D/L/fc/fy sampled within ranges, stored to 3 decimals.\nOne D and L apply to every span; Wu = 1.2D + 1.6L only. D is drawn evenly, then L within the band allowed by the live/dead ratio limits (default 0.25 ≤ L/D ≤ 1.5). Stirrup fyt is sampled on its own (default 200–420 MPa; ACI 318-19 Table 20.2.2.4(a) limits fyt for shear to 420 MPa). By default D is superimposed dead load (slab, finishes, walls) and ETABS adds the beam self-weight at 24 kN/m³. The section grid is 25 mm; span lengths use the span grid step (250 mm = 3.00, 3.25 … m).\nThe neighbouring span ratio (longer ÷ shorter) applies only when spans may differ; 0 means no limit.\nSection shapes follow 0.3h ≤ b ≤ h by default (607 of the 875 grid pairs); set a b/h limit to 0 to remove it.\nSpans of 4h or less are ACI deep beams; they are marked invalid and never sent to ETABS.\nUse 607 candidate sizes to test every allowed pair. Create makes inputs only; no analysis starts here.\nSmart size search ignores the shared candidate count: every allowed depth of each search width is listed and, per beam line, ETABS runs only the depths the halving search needs.", wraplength=self.px(750), foreground="#526879").grid(row=28, column=0, columnspan=3, sticky="w", pady=12)
        def read_ranges():
            values = {k: float(v.get()) for k, v in variables.items()}
            for key, value in defaults.items():
                if type(value) is int:
                    if values[key] != int(values[key]):
                        raise ValueError(f"{key} must be an integer")
                    values[key] = int(values[key])
            values.update(stirrup_matches_fy=match_fy.get(),equal_spans=equal_spans.get(),whole_unit_parameters=whole_units.get())
            try:
                widths = tuple(int(w) for w in search_widths.get().replace(";", ",").split(",") if w.strip())
            except ValueError:
                raise ValueError("Search widths must be whole millimetres separated by commas, e.g. 300, 400, 500, 600") from None
            values.update(size_search=size_search.get(), search_widths_mm=widths, dead_includes_self_weight=dead_total.get())
            return ResearchRanges(**values).validate()
        def show_factorial():
            try:
                ranges=read_ranges()
                counts=factorial_counts(ranges)
                share=100*counts['dead_live_pairs_allowed']/counts['dead_live_pairs']
                pairs_line=f"D–L pairs allowed by the L/D limits: {share:.1f}% of the D × L range\n"
                if ranges.size_search:
                    groups=search_groups(search_sections(ranges))
                    per_line=planned_model_count(1,search_sections(ranges),True)
                    info=(f"Smart size search\nSearch widths (mm): {', '.join(str(b) for b,_ in groups)}\n"
                          f"Grid depths per width: {', '.join(f'{b}: {len(d)}' for b,d in groups)}\n"
                          f"ETABS runs per beam line: at most {per_line}\nRequested sampled scenarios: {ranges.count:,}\n"
                          f"Planned ETABS models: at most {ranges.count*per_line:,} (fewer when deep-beam limits shorten a search)\n"
                          f"Allowed span arrangements per beam line: {counts['span_arrangements']:,}\n"+pairs_line+"\n"
                          "Each beam line gets one size target: the smallest passing size over the search widths. No ETABS run starts here.")
                elif not ranges.whole_unit_parameters:
                    minimum=ranges.count*ranges.candidate_count*ranges.spans_min
                    maximum=ranges.count*ranges.candidate_count*ranges.spans_max
                    rows=f"{minimum:,}" if minimum==maximum else f"{minimum:,}–{maximum:,}"
                    info=f"Span lengths on the grid: {counts['span_length_levels']:,}\nAllowed span arrangements per beam line: {counts['span_arrangements']:,}\nSection pairs on the grid: {counts['grid_section_pairs']:,}\nSection pairs allowed by the b/h limits: {counts['section_pairs']:,}\nRequested sampled scenarios: {ranges.count:,}\n"+pairs_line+f"Candidate sizes per scenario: {ranges.candidate_count:,}\nPlanned ETABS models: {ranges.count*ranges.candidate_count:,}\nPlanned beam-candidate rows: {rows}\n\nSpan/load/material ranges do not define a finite full-factorial count without explicit discrete levels.\nPassing rows are unknown until checks complete. No ETABS run starts here."
                else:
                    info=f"Integer-only grid estimate:\nBase beam-line scenarios: {counts['base_scenarios']:,}\nSection pairs: {counts['section_pairs']:,}\nCandidate ETABS models: {counts['candidate_models']:,}\nBeam-candidate rows: {counts['beam_candidate_rows']:,}\n\nPassing rows are unknown until checked. This is a count only."
                messagebox.showinfo("Variation count — no ETABS run",info,parent=dialog)
            except (ValueError,TypeError,OverflowError) as exc:
                messagebox.showerror("Check ranges",str(exc),parent=dialog)
        ttk.Button(frame,text="Variation count / study size (count only)",command=show_factorial).grid(row=29,column=0,columnspan=3,sticky="ew",pady=6)
        def add():
            try:
                template = self.editor_scenario()
                config = sample_study(read_ranges(), template)
                scenarios = config.to_dict()["scenarios"] if replace_current.get() else self.scenarios+config.to_dict()["scenarios"]
                # Validate before changing any of the user's existing study state.
                data = config.to_dict()
                data["scenarios"] = scenarios
                data["max_models"] = planned_model_count(len(scenarios), config.sections_mm, config.size_search)
                config = Config.parse(data)
                self.scenarios = config.to_dict()["scenarios"]
                self.sections = config.sections_mm
                self.sampling_settings = config.sampling_settings
                self.width_depth_limits = (config.min_width_depth_ratio, config.max_width_depth_ratio)
                self.min_span_depth_ratio = config.min_span_depth_ratio
                self.size_search.set(config.size_search)
                self.protocol_choice.set(next(iter(PROTOCOLS)))
                self.dead_basis_choice.set(next(k for k, v in DEAD_BASES.items() if v == config.dead_load_basis))
                self.range_limits.set(True)
                self.case_checks.set(True)
                self.refresh_scenarios()
                self.refresh_sections()
                self.load_editor(0)
                self.status.set(f"Created {len(self.scenarios)} scenarios and {len(self.sections)} candidate sizes. Save the study to preserve its exact inputs.")
                dialog.destroy()
            except (ValueError, TypeError, OverflowError) as exc:
                messagebox.showerror("Check sample ranges", str(exc), parent=dialog)
        ttk.Button(frame, text="Create sampled study", style="Accent.TButton", command=add).grid(row=30, column=0, columnspan=3, sticky="ew")
        dialog.grab_set()

    def close(self):
        if self.busy:
            self.stop()
            self.status.set("Finishing the current model before closing is available. Please wait, then close the window.")
        else:
            self.destroy()


def main():
    BeamLab().mainloop()


if __name__ == "__main__":
    main()
