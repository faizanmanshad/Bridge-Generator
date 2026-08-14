# Bridge Generator

> A modular pyRevit-based Autodesk Revit automation project for progressively generating and configuring bridge models.

## Project Overview

Bridge Generator is being developed to automate repetitive bridge modelling and setup operations inside Autodesk Revit.

The project is deliberately modular rather than attempting to generate a complete bridge with one massive script. By breaking the tool down, the long-term system may eventually automate:

- Bridge setup
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

*(Note: Not all of these features are implemented yet.)*

## Development Philosophy

The core development principle is:

```text
Design
    ↓
Document
    ↓
Implement one subsystem
    ↓
Test in Revit
    ↓
Stabilize
    ↓
Commit
    ↓
Move to the next subsystem
```

The project deliberately avoids implementing the complete bridge generator at once. This strategy makes debugging easier, Revit API failures simpler to isolate, regression testing more manageable, version compatibility safer, and the overall architecture easier to maintain.

## How the Project Started

The project has followed a structured evolution:

### Stage 1 — Initial Idea
The project began with the goal of automating bridge creation and setup inside Autodesk Revit. Requirements, workflows, bridge components, dependencies, and automation possibilities were explored before writing the production plugin.

### Stage 2 — ChatGPT Planning
Long-form project information and requirements were discussed with ChatGPT, which served as a planning layer, architecture reasoning layer, debugging assistant, and master-prompt generator. Instead of immediately generating large amounts of code, detailed engineering prompts were produced.

### Stage 3 — Master Prompt
A detailed master prompt describing the intended Bridge Generator architecture and development rules was produced.

### Stage 4 — Claude Documentation
The master prompt was provided to Claude to translate the project requirements into structured development and reference documents. 

The repository currently contains the following core documentation:

- **PRD.md**: Product Requirements Document outlining functional scope.
- **ARCHITECTURE.md**: System architecture and environment constraints.
- **COMPATIBILITY.md**: Revit / pyRevit / API compatibility considerations.
- **DESIGN.md**: Detailed design reference, parameter tables, and implementation decisions.
- **MEMORY.md**: Persistent context and continuity information for AI agents.
- **PHASES.md**: Implementation sequence and test plan.
- **RULES.md**: Hard constraints and development rules.

### Stage 5 — Antigravity Implementation
Antigravity is being used as the main AI-assisted development environment for editing and implementing the actual repository. The workflow is approximately:

```text
Problem / requirement
        ↓
ChatGPT analysis
        ↓
Detailed Antigravity engineering prompt
        ↓
Antigravity inspects repository
        ↓
Small targeted implementation
        ↓
Actual Revit runtime testing
        ↓
Fix first real failure
        ↓
Regression test
```

AI-generated code is not assumed to be correct out of the box; actual Autodesk Revit runtime testing remains authoritative.

## Technology Stack

- Autodesk Revit 2022
- Autodesk Revit 2024.3
- pyRevit 6.4.0
- IronPython / .NET
- Autodesk Revit API
- WPF / XAML
- Git
- GitHub

## pyRevit Extension Structure

The project utilizes pyRevit's directory naming conventions to construct the Revit ribbon UI. The generic structure is as follows:

```text
<RevitExtensionsRoot>/
└── <ExtensionName>.extension/
    └── <TabName>.tab/
        └── BridgeGenerator.panel/
            └── ReferencePlane.pushbutton/
                ├── script.py
                ├── ui/
                ├── core/
                └── supporting resources
```

- `.extension` defines a pyRevit extension.
- `.tab` creates a Revit ribbon tab.
- `.panel` creates a ribbon panel.
- `.pushbutton` creates an executable pyRevit pushbutton.

## Current Implemented Pushbutton

The currently developed tool is `ReferencePlane.pushbutton`. Historically named for reference planes, the tool now does considerably more, serving as the entry point that opens the primary **Bridge Setup workflow**.

## Current Bridge Setup Workflow

The current tab order for the Bridge Setup interface is strictly ordered as:

1. **Global Parameters**
2. **Bridge Configuration**
3. **Reference Planes**

This logical order exists because Global Parameters must exist first; Bridge Configuration then writes controlling values to them; and finally, the Reference Plane system uses the configured project state to generate geometry.

## Global Parameters

The plugin contains a structured Global Parameter system used to control bridge geometry. Important directly controlled configuration parameters include:

- Length
- Clear Span
- Camber
- Crank Length

Additional parameters may be formula-driven. Bridge Configuration updates controlling inputs and allows Revit formulas to recalculate dependent values.

## Bridge Configuration

Current bridge configuration work is primarily focused on:

- Timber Deck Bridge
- CRNK / Cranked configuration

The UI concepts include: Span, Width, CRNK Configuration, Camber, and Families. The UI maps these concepts directly to the parameters:

- Span → Length
- Width → Clear Span
- Camber → Camber
- CRNK geometry → Crank Length

Currently supported configurations span from 4 m to 24 m in length (in 2 m increments) and 1.5 m to 3.0 m in width (in 0.5 m increments). Span determines the available CRNK configurations, and this mapping is maintained centrally in the code.

## Family Selection

Bridge components currently considered in the configuration UI include: Beam, Bearer, Joist, and Packer.

The family-loading architecture follows this flow:

```text
Select component
        ↓
Browse to RFA
        ↓
Inspect available Family Types
        ↓
Choose required type
        ↓
Load selected type
```

The intended system avoids unnecessarily loading an entire multi-type family merely to use one specific type.

## Reference Plane System

The Reference Plane subsystem establishes the bridge-driving geometry. At a high level, it includes:

- Transverse Reference Planes
- Longitudinal Reference Planes
- Center planes
- Intermediate planes
- Crank locations
- End locations
- Dimensions
- Equality constraints where applicable
- Global Parameter-driven dimensions

The operation requires an appropriate Revit project Floor Plan, and a supported plan-view pre-flight check is intentionally included.

## Revit 2022 + Revit 2024 Strategy

The architectural requirement for multi-version support is:

```text
ONE shared extension
        ↓
Shared business logic
        ↓
Small compatibility boundary
        ↓
Revit 2022 / Revit 2024.3
```

Version-specific code should exist only where Autodesk Revit API differences genuinely require it, rather than maintaining completely separate copies for each version.

## Runtime Testing Philosophy

Editor and static-analysis warnings are not automatically considered actual Revit failures. The project distinguishes between an `IDE / static-analysis diagnostic` and an `actual Autodesk Revit runtime error`. 

The debugging strategy is:

```text
Find first real runtime failure
        ↓
Fix smallest root cause
        ↓
Rerun Revit
        ↓
Capture next first failure
        ↓
Regression test both versions
```

## Documentation

- [Product Requirements](ReferencePlane.pushbutton/PRD.md)
- [Architecture](ReferencePlane.pushbutton/ARCHITECTURE.md)
- [Compatibility](ReferencePlane.pushbutton/COMPATIBILITY.md)
- [Detailed Design](ReferencePlane.pushbutton/DESIGN.md)
- [Project Memory](ReferencePlane.pushbutton/MEMORY.md)
- [Phases & Test Plan](ReferencePlane.pushbutton/PHASES.md)
- [Development Rules](ReferencePlane.pushbutton/RULES.md)

## Current Development Status

- **Current phase:** Bridge Setup stabilization and multi-version compatibility.
- **Current primary tool:** `ReferencePlane.pushbutton`
- **Current major systems:**
  - Global Parameters
  - Bridge Configuration
  - Family selection
  - Reference Plane generation
  - Dimension / constraint integration
  - Revit 2022 + 2024.3 compatibility

## Planned Future Development

- Main structural framing
- Timber deck
- Concrete deck workflows
- Connections
- Bracing
- Handrails
- Additional bridge components
- Validation
- Further automation

*(These remain planned future work and are not yet fully implemented.)*

## Git / GitHub Workflow

### Main Branch
Use `main` for:
- Stable changes
- Verified bug fixes
- Documentation
- Changes where behavior is understood and low risk

### Feature / Experimental Branches
Use separate branches for:
- Uncertain Revit API approaches
- Experimental geometry
- Potentially breaking refactors
- Major compatibility experiments
- Features whose runtime behavior is not yet known

Conceptual workflow:
```text
main
  │
  ├── documentation / safe fixes
  │
  └── feature branch
          ↓
       implement
          ↓
       Revit test
          ↓
       regression test
          ↓
       merge into main
```

## Contributing / Development Note

Development is currently project-specific and rapidly evolving.
