# Bridge Generator

> A modular pyRevit-based Autodesk Revit automation project for progressively configuring and generating Urbana bridge models.

## Project Overview

Bridge Generator is an in-progress Autodesk Revit automation project built with pyRevit, IronPython/.NET, the Autodesk Revit API, and WPF/XAML.

The project is intentionally modular. Instead of attempting to generate an entire bridge through one large script, the workflow is being developed as a sequence of controlled subsystems that can be tested independently inside Revit.

The long-term system is intended to progressively automate:

- Bridge setup
- Family loading
- Family parameter mapping
- Bridge configuration
- Global parameters
- Reference geometry
- Beams
- Bearers
- Packers
- Joists
- Bearings
- Decks
- Structural connections
- Bracing
- Handrails
- Additional bridge components

> **Development status:** the plugin is still under active development. The current work has established the core/backbone of the Bridge Setup workflow, but the complete bridge generator is not finished yet.

---

## Development Philosophy

The project follows a staged development process:

```text
Design
    ↓
Document
    ↓
Implement one subsystem
    ↓
Test inside Autodesk Revit
    ↓
Fix the first real runtime failure
    ↓
Stabilize
    ↓
Commit
    ↓
Move to the next subsystem
```

The goal is to keep failures isolated and understandable instead of allowing unrelated Revit API, UI, geometry, family, and parameter problems to become mixed together.

Important development principles include:

- Prefer small, testable changes over large rewrites.
- Preserve known-good Revit functionality while adding new features.
- Treat actual Autodesk Revit runtime behavior as authoritative.
- Keep Revit 2022 and Revit 2024.3 support inside one shared extension where practical.
- Reuse existing validated Dynamo automation where it remains useful.
- Keep temporary Bridge Setup session state separate from persistent Revit document state.
- Use feature branches for uncertain or experimental work, then merge only after runtime stabilization.

---

## How the Project Started

### Stage 1 — Initial Idea

The project began with the goal of automating repetitive bridge modelling and setup operations inside Autodesk Revit.

Before implementation, the workflow, bridge components, dependencies, Global Parameters, family requirements, reference geometry, Dynamo logic, and Revit API constraints were explored.

### Stage 2 — ChatGPT Planning

ChatGPT has been used primarily as the planning, architecture, debugging, and master-prompt layer for the project.

The workflow is generally:

```text
Requirement / Revit problem
        ↓
ChatGPT analysis
        ↓
Detailed implementation prompt
        ↓
Antigravity repository inspection
        ↓
Targeted implementation
        ↓
Actual Revit runtime testing
```

### Stage 3 — Project Documentation

The project was documented through several reference files so architecture and implementation decisions remain consistent across development.

The repository currently contains documentation such as:

- **PRD.md** — Product Requirements Document
- **ARCHITECTURE.md** — System architecture and environment constraints
- **COMPATIBILITY.md** — Revit / pyRevit / API compatibility considerations
- **DESIGN.md** — Detailed implementation and parameter design
- **MEMORY.md** — Project continuity/reference information
- **PHASES.md** — Implementation sequence and testing plan
- **RULES.md** — Development constraints and rules

### Stage 4 — Antigravity Implementation

Antigravity is being used as the primary AI-assisted code-editing environment.

The code is not assumed to be correct simply because it was generated or statically reviewed. Runtime testing inside Autodesk Revit remains mandatory.

---

## Technology Stack

Current development environment:

- Autodesk Revit 2022
- Autodesk Revit 2024.3
- pyRevit 6.4.0
- IronPython / .NET
- Autodesk Revit API
- WPF / XAML
- Git
- GitHub
- Dynamo where existing bridge automation remains useful

---

## pyRevit Structure

pyRevit discovers extensions through a folder structure such as:

```text
<PyRevitExtensionRoot>/
└── <AnyName>.extension/
    └── <AnyName>.tab/
        └── BridgeGenerator.panel/
            ├── README.md
            ├── docs/
            │   └── screenshots/
            └── ReferencePlane.pushbutton/
                ├── script.py
                ├── core/
                ├── ui/
                ├── PRD.md
                ├── ARCHITECTURE.md
                ├── COMPATIBILITY.md
                ├── DESIGN.md
                ├── MEMORY.md
                ├── PHASES.md
                ├── RULES.md
                └── supporting resources
```

The extension name and ribbon tab name are not hard requirements of this repository. A user may choose their own:

```text
<AnyName>.extension
<AnyName>.tab
```

The important requirement is that the final pyRevit structure remains valid and that pyRevit is configured to scan the parent extension root.

The current pushbutton is physically named:

```text
ReferencePlane.pushbutton
```

The name is historical. The tool now performs considerably more than Reference Plane creation and currently acts as the main **Bridge Setup** interface.

A future rename may be considered, but the bundle should not be renamed casually while unrelated functionality is being stabilized.

---

# Current Bridge Setup Workflow

The current Bridge Setup workflow is:

```text
① Load Families
    ├── Load Families
    └── Parameter Mapping

② Bridge Configuration

③ Global Parameters

④ Reference Planes
```

Each stage has a separate responsibility.

---

## 1. Load Families

The first main tab is responsible for loading structural family types required for the current timber bridge setup.

Current components:

- Beam
- Bearer
- Joist
- Packer

The working loading flow is:

```text
Select component
        ↓
Browse to .rfa
        ↓
Read available Family Types
        ↓
Select required type
        ↓
Load Selected Families
        ↓
Selected Family Type is loaded into the active Revit document
```

The plugin intentionally loads the selected family/type rather than blindly loading unnecessary content.

Each component is handled independently. This allows a user to replace only one component later—for example, a Beam—while preserving the existing Bearer, Joist, and Packer.

---

## 1A. Parameter Mapping

`Parameter Mapping` is a sub-tab inside the main `Load Families` stage.

It is dependent on successful family loading.

Once a component has been successfully loaded into the active Revit document, that component becomes available for mapping. The plugin then works from the **actual FamilySymbol loaded in the host Revit document**, rather than relying on temporary external `.rfa` family objects as the long-term source of truth.

The purpose of mapping is to allow the user to select the real family parameter that corresponds to a logical bridge dimension.

This is necessary because Revit family parameter names are not guaranteed to be consistent. For example, the actual height parameter may be named `Height`, `h`, `d`, `Depth`, or something else depending on the family.

### Current Semantic Mapping Roles

#### Beam

- Height
- Width
- Web Thickness
- Flange Thickness

#### Bearer

- Width

#### Joist

- Height

#### Packer

- Height
- Width

The user selects the actual family parameter from a dropdown. The plugin reads its value from the loaded FamilySymbol and stages the resulting numeric values for the Global Parameters workflow.

The mapping is finalized through:

```text
Apply Parameter Mapping
```

Parameter Mapping itself does **not** create Global Parameters.

---

## Family Parameter → Global Parameter Mapping

The current authoritative family-derived mappings are:

| Component | Selected Family Parameter | Global Parameter Input |
|---|---|---|
| Beam | Height | `UB_Height` |
| Beam | Width | `Beam Centerline = Beam Width / 2` |
| Beam | Web Thickness | `Beam Web` |
| Beam | Flange Thickness | `Beam Flange` |
| Bearer | Width | `Bearers Width` |
| Bearer | Width | `Bearer Width` |
| Joist | Height | `Vertical_Joist_Height` |
| Packer | Height | `GP_Framing_Height` |
| Packer | Height | `Horizontal_Joist_Height` |
| Packer | Width | `Horizontal_Joist_Width` |

`Bearers Width` and `Bearer Width` are intentionally two separate existing Global Parameters.

Likewise, formula-driven Global Parameters must not be confused with their input parameters.

For example:

```text
Vertical_Joist_Height
```

is an input, while:

```text
GP_Vertical_Joists_Height
```

is formula-driven.

---

## 2. Bridge Configuration

The Bridge Configuration tab stages bridge geometry inputs.

Current configuration is focused primarily on the Timber Deck Bridge and CRNK / cranked bridge geometry.

Current UI inputs include:

- Span
- Width
- CRNK Configuration
- Camber

Current mapping:

| UI Input | Staged Global Parameter Input |
|---|---|
| Span | `Length` |
| Width | `Clear Span` |
| CRNK Configuration | `Crank Length` |
| Camber | `Camber` |

Current supported spans:

```text
4 m
6 m
8 m
10 m
12 m
14 m
16 m
18 m
20 m
22 m
24 m
```

Current supported widths:

```text
1.5 m
2.0 m
2.5 m
3.0 m
```

The CRNK configuration options depend on span.

Bridge Configuration values are staged in the current Bridge Setup session and are only treated as updates after the user explicitly applies the configuration.

Merely reopening the plugin or viewing the default controls must not overwrite the existing Revit project configuration.

---

## 3. Global Parameters

The Global Parameters tab creates, validates, and selectively updates the bridge Global Parameter system.

The project currently uses a structured set of approximately 25 Global Parameters grouped under Revit categories such as:

- Construction
- Dimensions
- Other

Important input parameters include:

```text
Length
Clear Span
Camber
Crank Length

UB_Height
Beam Centerline
Beam Web
Beam Flange

Bearers Width
Bearer Width

Vertical_Joist_Height

GP_Framing_Height
Horizontal_Joist_Height
Horizontal_Joist_Width
```

### Persistent Revit State vs Temporary Session State

An important architectural rule is:

```text
Revit document
    =
persistent baseline

Current Bridge Setup session
    =
optional pending changes
```

This means an already-configured project does **not** need to repeat the full setup sequence every time Bridge Setup is reopened.

If the user closes and reopens Bridge Setup, then goes directly to:

```text
③ Global Parameters
```

and clicks:

```text
Create / Update Global Parameters
```

the plugin inspects the existing Revit Global Parameters first.

If nothing new has been staged:

```text
Existing Revit GP state
        ↓
Validate
        ↓
Display existing values/formulas
        ↓
No unnecessary writes
```

If only Bridge Configuration changed, only configuration-derived inputs are updated.

If only one family/component was replaced and remapped, only the relevant family-derived Global Parameter inputs are updated.

This makes `Create / Update Global Parameters` idempotent and safer to re-run.

---

## Global Parameter Formula Strategy

Formula-driven parameters remain controlled by Revit formulas rather than being manually calculated and overwritten in Python.

Known relationships include:

```text
GP_Horizontal_Joist_Height
=
Horizontal_Joist_Height + GP_Framing_Height
```

```text
GP_Vertical_Joists_Height
=
Vertical_Joist_Height + GP_Framing_Height
```

```text
GP_UB_Height
=
UB_Height + GP_Horizontal_Joist_Height
```

```text
Bearers Length
=
Clear Span - Beam Centerline - Beam Web
```

```text
Vertical Joist Bounding Distance
=
Clear Span - 2 * Beam Centerline
```

The plugin provides the required input values and lets Revit recalculate dependent formulas.

---

## 4. Reference Planes

The Reference Plane subsystem uses the configured Global Parameters to establish bridge-driving geometry.

The system includes concepts such as:

- Longitudinal Reference Planes
- Transverse Reference Planes
- Center planes
- Intermediate planes
- Crank locations
- End locations
- Dimensions
- Global Parameter-driven dimensions
- Equality constraints where appropriate

The operation requires an appropriate Revit project Floor Plan.

Current normal longitudinal topology includes:

```text
Left End
Left Intermediate
Left Crank
V_Center
Right Crank
Right Intermediate
Right End
```

Special `CRNK - 2/2` topology removes the crank planes:

```text
Left End
Left Intermediate
V_Center
Right Intermediate
Right End
```

Reference Plane prerequisites are checked against persistent Global Parameters in the active Revit document.

---

# Current Core / Backbone Status

The project is still under development, but the primary Bridge Setup backbone has now been established.

Current working/stabilized areas include:

- Dedicated Load Families stage
- Beam / Bearer / Joist / Packer family-type loading
- Independent component replacement workflow
- Parameter Mapping sub-tab
- Loaded-family parameter extraction from host Revit FamilySymbols
- Apply Parameter Mapping workflow
- Family-derived GP input mapping
- Bridge Configuration
- Session-staged configuration changes
- Global Parameter creation
- Global Parameter validation
- Selective Global Parameter updates
- Persistent existing-GP behavior after reopening Bridge Setup
- Formula-driven GP relationships
- Reference Plane prerequisites and generation architecture
- Shared Revit 2022 / Revit 2024.3 development strategy

This should be treated as the current foundation for later bridge-generation systems rather than as the final completed plugin.

---

# Screenshots / Visual Workflow

Runtime screenshots are included in the repository under:

```text
docs/screenshots/
```

These screenshots show the current Bridge Setup workflow and are intended to help users and developers understand how the plugin behaves inside Autodesk Revit.

They cover:

- Urbana / Bridge Generator ribbon access
- Load Families
- Successfully loaded families
- Parameter Mapping
- Successfully mapped parameters
- Bridge Configuration
- Global Parameter creation
- Reference Plane creation
- Reference Planes in the project view
- Imported families in the Revit project

Browse them here:

[Open the screenshot folder](docs/screenshots/)

Because the screenshots are stored using a repository-relative path, they remain portable when the repository is cloned to another computer.

---

# Installation

## Prerequisites

Before installing the plugin, ensure that the following are available:

- Autodesk Revit 2022 and/or Autodesk Revit 2024.3
- pyRevit 6.4.0 or a compatible pyRevit installation
- Windows
- Git, if cloning the repository
- Access to the Revit family `.rfa` files required for the bridge workflow

> Family libraries may be stored anywhere on the user's computer or network. The plugin does not require the user to reproduce any developer-specific family-library path.

Because the project is still under development, test the plugin in a controlled Revit project before using it on production work.

---

## Recommended pyRevit Installation Layout

The repository should sit inside a valid pyRevit extension/tab structure.

A generic example is:

```text
<PyRevitExtensionRoot>/
└── <YourExtensionName>.extension/
    └── <YourTabName>.tab/
        └── BridgeGenerator.panel/
            ├── README.md
            ├── docs/
            └── ReferencePlane.pushbutton/
```

The user may choose any valid names for:

```text
<YourExtensionName>.extension
<YourTabName>.tab
```

The important part is that pyRevit is configured to scan:

```text
<PyRevitExtensionRoot>
```

as an extension directory.

---

## Installation by Cloning the Repository

### 1. Create a pyRevit extension root

Choose any convenient folder on the computer.

Example conceptually:

```text
<PyRevitExtensionRoot>/
```

Do not use a developer-specific path from this documentation; choose a location appropriate for the user's environment.

### 2. Create the pyRevit extension and tab folders

Inside the chosen extension root, create:

```text
<YourExtensionName>.extension/
└── <YourTabName>.tab/
```

For example, the names may be company-specific or project-specific.

### 3. Clone the repository into the tab structure

Clone the repository so the resulting project forms the Bridge Generator panel inside the selected tab.

Conceptually:

```text
<PyRevitExtensionRoot>/
└── <YourExtensionName>.extension/
    └── <YourTabName>.tab/
        └── BridgeGenerator.panel/
```

Repository:

```text
git@github.com:faizanmanshad/Bridge-Generator.git
```

If the repository is cloned directly into the tab folder, make sure the cloned folder is ultimately named:

```text
BridgeGenerator.panel
```

or rename the cloned repository folder to that pyRevit panel name after cloning.

Example:

```powershell
cd "<path-to-your-tab-folder>"
git clone git@github.com:faizanmanshad/Bridge-Generator.git BridgeGenerator.panel
```

This produces:

```text
<YourTabName>.tab/
└── BridgeGenerator.panel/
```

### 4. Add the extension root to pyRevit

Configure pyRevit to load extensions from:

```text
<PyRevitExtensionRoot>
```

The exact folder can be different for every user.

The important point is that pyRevit must know the parent directory containing:

```text
<YourExtensionName>.extension
```

### 5. Reload pyRevit / Restart Revit

After adding the extension path:

```text
1. Reload pyRevit, or restart Autodesk Revit.
2. Open the custom ribbon tab created by <YourTabName>.tab.
3. Find the Bridge Generator panel.
4. Launch the Bridge Setup pushbutton.
```

---

## Installation Without Git

The same structure can be created manually.

Copy the repository contents into:

```text
<PyRevitExtensionRoot>/
└── <YourExtensionName>.extension/
    └── <YourTabName>.tab/
        └── BridgeGenerator.panel/
```

Then add `<PyRevitExtensionRoot>` to pyRevit's configured extension directories and reload pyRevit / Revit.

---

## Family Library Location

The plugin allows the user to browse for Revit family files.

Family libraries do **not** need to exist at a specific hard-coded path.

The user may keep `.rfa` families in:

- Autodesk Revit content libraries
- A company family library
- A network location
- A project-specific family folder
- Any other accessible location

The workflow is simply:

```text
Load Families
    ↓
Browse...
    ↓
select the required .rfa
    ↓
select the required Family Type
    ↓
Load Selected Families
```

---

# Basic Usage

For a new bridge setup, the intended initial sequence is:

```text
① Load Families
        ↓
Load required component types

① Parameter Mapping
        ↓
Select actual family parameters
        ↓
Apply Parameter Mapping

② Bridge Configuration
        ↓
Select Span / Width / CRNK / Camber
        ↓
Apply Bridge Configuration

③ Global Parameters
        ↓
Create / Update Global Parameters

④ Reference Planes
        ↓
Create / Update Reference Planes
```

For an already-configured Revit project, each tab is intended to work more independently.

For example:

```text
Open Bridge Setup
        ↓
go directly to Global Parameters
        ↓
Create / Update Global Parameters
        ↓
existing values are validated/displayed
        ↓
nothing is rewritten unless a new change was staged
```

Likewise, changing only a Beam should not require rebuilding the entire configuration.

---

# Multi-Version Strategy

The intended architecture remains:

```text
ONE shared pyRevit extension
        ↓
Shared business logic
        ↓
Small compatibility boundary where required
        ↓
Revit 2022 / Revit 2024.3
```

Separate full copies of the plugin should not be maintained for each Revit version unless an unavoidable Revit API incompatibility requires it.

---

# Runtime Testing Philosophy

The project distinguishes between:

```text
IDE / static-analysis warning
```

and:

```text
actual Autodesk Revit runtime failure
```

This matters because modules such as:

```text
clr
Autodesk.Revit.DB
System.Windows
Microsoft.Win32
```

are provided by the Revit / pyRevit / .NET runtime and may not resolve correctly in an unrelated desktop Python static-analysis environment.

The debugging workflow remains:

```text
Find first real Revit runtime failure
        ↓
Fix smallest root cause
        ↓
Rerun Revit
        ↓
Capture the next first failure
        ↓
Regression test
```

---

# Documentation

Additional project documentation:

- [Product Requirements](ReferencePlane.pushbutton/PRD.md)
- [Architecture](ReferencePlane.pushbutton/ARCHITECTURE.md)
- [Compatibility](ReferencePlane.pushbutton/COMPATIBILITY.md)
- [Detailed Design](ReferencePlane.pushbutton/DESIGN.md)
- [Project Memory](ReferencePlane.pushbutton/MEMORY.md)
- [Phases & Test Plan](ReferencePlane.pushbutton/PHASES.md)
- [Development Rules](ReferencePlane.pushbutton/RULES.md)
- [Runtime Screenshots](docs/screenshots/)

---

# Git / GitHub Workflow

Repository:

```text
git@github.com:faizanmanshad/Bridge-Generator.git
```

## Main Branch

`main` is intended for stable, understood, runtime-verified work.

## Feature / Experimental Branches

Experimental or uncertain development should occur on a separate branch.

The separate family-loader and Parameter Mapping work was developed on:

```text
feature/Seperate-Family-Loader-Tab
```

This feature branch was used to protect the stable baseline while the separate family-loading and Parameter Mapping architecture was being developed and tested.

The resulting Bridge Setup backbone can be merged into `main` once runtime verification and documentation are complete.

Conceptual workflow:

```text
main
  │
  └── feature branch
          ↓
       develop
          ↓
       Revit runtime test
          ↓
       stabilize
          ↓
       document
          ↓
       merge into main
```

---

# Planned Future Development

The current Bridge Setup backbone is only the beginning of the full Bridge Generator.

Planned future systems include:

- Main structural framing generation
- Timber deck generation
- Concrete deck workflows
- Structural connections
- Bracing
- Handrail automation
- Additional bridge components
- Validation and QA workflows
- Further multi-version testing
- Potential controlled reuse/integration of existing Dynamo bridge automation

---

# Contributing / Development Note

This project is actively evolving.

When modifying it:

- Preserve working Revit behavior.
- Avoid unnecessary architecture rewrites.
- Use feature branches for uncertain changes.
- Test inside the target Revit version.
- Do not assume static-analysis success equals Revit runtime success.
- Update documentation when architecture or parameter ownership changes.
- Avoid introducing machine-specific paths into source-controlled documentation.

The current Bridge Setup workflow should be treated as the baseline foundation for the next stages of bridge automation.
