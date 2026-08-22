# Bridge Generator

> A modular pyRevit-based Autodesk Revit automation project for progressively configuring and generating Urbana bridge models.

## Project Overview

Bridge Generator is an in-progress Autodesk Revit automation project being developed for Urbana using pyRevit, IronPython/.NET, the Autodesk Revit API, and WPF/XAML.

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

The goal is to keep failures isolated and understandable instead of allowing multiple unrelated Revit API, UI, geometry, family, and parameter problems to become mixed together.

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

The project was documented through several reference files so that architecture and implementation decisions remain consistent across development.

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

## Repository / pyRevit Structure

The current plugin is organized using pyRevit extension conventions.

```text
Urbana.extension/
└── Urbana.tab/
    └── BridgeGenerator.panel/
        ├── README.md
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

The pushbutton is still physically named:

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

The first main tab is responsible for loading the structural family types required for the current timber bridge setup.

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

Each component is handled independently. This is important because a user may later replace only one component, such as the Beam, while preserving the existing Bearer, Joist, and Packer.

---

## 1A. Parameter Mapping

`Parameter Mapping` is a sub-tab inside the main `Load Families` stage.

It is dependent on successful family loading.

Once a component has been successfully loaded into the active Revit document, that component becomes available for mapping. The plugin then works from the **actual FamilySymbol loaded in the host Revit document**, rather than keeping temporary external `.rfa` family objects as the long-term source of truth.

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

An important architectural change has been implemented:

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

the plugin should inspect the existing Revit Global Parameters first.

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

Runtime screenshots have been added to the plugin repository so future users and developers can see how the Bridge Setup interface works inside Autodesk Revit.

They demonstrate areas such as:

- Load Families
- Parameter Mapping
- Bridge Configuration
- Global Parameters
- Reference Planes
- Runtime status / validation states

**Screenshot folder:**

```text
<ADD_ACTUAL_SCREENSHOT_FOLDER_PATH_HERE>
```

> Replace the placeholder above with the final screenshot folder path inside the plugin repository once confirmed.

When adding screenshots to GitHub, relative repository paths are preferred so the documentation continues to work when the repository is cloned to another computer.

Example Markdown once filenames are known:

```markdown
![Load Families](relative/path/to/screenshots/load-families.png)
![Parameter Mapping](relative/path/to/screenshots/parameter-mapping.png)
![Bridge Configuration](relative/path/to/screenshots/bridge-configuration.png)
![Global Parameters](relative/path/to/screenshots/global-parameters.png)
![Reference Planes](relative/path/to/screenshots/reference-planes.png)
```

---

# Installation

## Prerequisites

The current plugin is being developed/tested with:

- Autodesk Revit 2022 and/or Autodesk Revit 2024.3
- pyRevit 6.4.0
- A Windows environment capable of running the relevant Autodesk Revit version
- Access to the required Urbana / Revit family libraries

Because the project is still under development, users should test the plugin in a controlled Revit project before using it on production work.

---

## Installation Option 1 — Clone the Repository

Clone the repository:

```powershell
git clone git@github.com:faizanmanshad/Bridge-Generator.git
```

Place or clone the extension into a directory that pyRevit is configured to use as an extension root.

The expected structure must ultimately contain:

```text
Urbana.extension/
└── Urbana.tab/
    └── BridgeGenerator.panel/
        └── ReferencePlane.pushbutton/
```

Then reload pyRevit or restart Autodesk Revit.

---

## Installation Option 2 — Copy the Extension Manually

Copy:

```text
Urbana.extension
```

into one of the custom extension directories configured in pyRevit.

Do not copy only `ReferencePlane.pushbutton`; keep the required pyRevit extension/tab/panel structure intact.

After copying:

```text
1. Open Autodesk Revit.
2. Confirm pyRevit is loaded.
3. Reload pyRevit or restart Revit.
4. Open the Urbana ribbon tab.
5. Locate the Bridge Generator panel.
6. Launch the Bridge Setup pushbutton.
```

---

## Current Development Path

The current development environment uses a structure similar to:

```text
D:\All Revit\19 Revit Plugins\RevitExtensions\
Urbana.extension\
Urbana.tab\
BridgeGenerator.panel\
ReferencePlane.pushbutton\
```

This is a development-machine path and should **not** be assumed to be the installation path for every user.

The important requirement is that pyRevit knows the parent directory containing:

```text
Urbana.extension
```

---

## Family Library Paths

Current family-loading workflows browse Revit family libraries.

Example development roots:

### Revit 2022

```text
D:\REVIT 2022\Libraries
```

### Revit 2024 / 2024.3

```text
D:\REVIT 2024\Libraries
```

These are current project/development paths and may differ on another computer.

If the required family library exists elsewhere, browse to the appropriate `.rfa` manually through the Load Families interface.

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

This branch was intentionally created so the previous `main` baseline remained recoverable if the loader/mapping experiment failed.

The feature has now established a sufficiently stable new Bridge Setup backbone to be merged back into `main` after the final documentation commit and normal verification.

Conceptual workflow:

```text
main
  │
  └── feature/Seperate-Family-Loader-Tab
          ↓
       develop
          ↓
       Revit runtime test
          ↓
       stabilize
          ↓
       commit
          ↓
       update documentation
          ↓
       merge into main
```

---

# Merging the Current Feature Branch into Main

Before merging, confirm the working tree is clean and the feature branch has been pushed.

Current branch:

```text
feature/Seperate-Family-Loader-Tab
```

Recommended sequence after the README update is committed:

```powershell
git status
git push origin feature/Seperate-Family-Loader-Tab

git switch main
git pull origin main

git merge --no-ff feature/Seperate-Family-Loader-Tab

git push origin main
```

If Git reports a merge conflict:

```text
1. Resolve the conflicting files manually.
2. Verify the intended code is preserved.
3. Stage the resolved files.
4. Complete the merge commit.
5. Runtime-test again if the conflict touched executable plugin code.
6. Push main.
```

After the merge has been verified, the feature branch may be kept for history or deleted later.

Optional local deletion:

```powershell
git branch -d feature/Seperate-Family-Loader-Tab
```

Optional remote deletion:

```powershell
git push origin --delete feature/Seperate-Family-Loader-Tab
```

Do not delete the feature branch until the merge into `main` has been confirmed and the merged plugin has been checked.

---

# Planned Future Development

The current Bridge Setup backbone is only the beginning of the full bridge generator.

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

This project is currently project-specific and actively evolving.

When modifying it:

- Preserve working Revit behavior.
- Avoid unnecessary architecture rewrites.
- Use feature branches for uncertain changes.
- Test inside the target Revit version.
- Do not assume static-analysis success equals Revit runtime success.
- Update documentation when architecture or parameter ownership changes.

The current Bridge Setup workflow should be treated as the baseline foundation for the next stages of bridge automation.
