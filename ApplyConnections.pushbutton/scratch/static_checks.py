
# static_checks.py  -- run with CPython to verify static invariants
import ast, re, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

files = [
    os.path.join(BASE, 'core', 'connection_catalog.py'),
    os.path.join(BASE, 'ui',   'ApplyConnectionsWindow.py'),
]

errors = []

# ------------------------------------------------------------------
# CHECK 1: Python syntax
# ------------------------------------------------------------------
for path in files:
    with open(path, 'rb') as fh:
        src = fh.read()
    try:
        ast.parse(src)
        print('SYNTAX OK:', os.path.basename(path))
    except SyntaxError as e:
        errors.append('SYNTAX ERROR in {}: {}'.format(os.path.basename(path), e))

# ------------------------------------------------------------------
# CHECK 2: No f-strings (IronPython 2.7 compat)
# ------------------------------------------------------------------
BAD_FSTRING = re.compile(r'f["\']')
for path in files:
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for i, line in enumerate(fh, 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if BAD_FSTRING.search(line):
                errors.append('F-STRING at {}:{}: {}'.format(os.path.basename(path), i, line.rstrip()))
print('CHECK 2: f-string scan complete ({} errors so far)'.format(len(errors)))

# ------------------------------------------------------------------
# CHECK 3: Backend phrases untouched in ApplyConnectionsWindow.py
# ------------------------------------------------------------------
with open(files[1], 'r', encoding='utf-8', errors='replace') as fh:
    win_src = fh.read()

required = [
    'REQUEST_APPLY_BEAM_BEAM',
    'REQUEST_APPLY_BEARER_BEAM',
    'REQUEST_APPLY_BRACING_CONNECTION',
    'self._ext_event.Raise',
    'validate_connection_selection',
    'validate_main_beam',
]
for phrase in required:
    if phrase not in win_src:
        errors.append('MISSING backend phrase: ' + phrase)
print('CHECK 3: backend phrase scan complete')

# ------------------------------------------------------------------
# CHECK 4: Classification symbols in connection_catalog.py
# ------------------------------------------------------------------
with open(files[0], 'r', encoding='utf-8', errors='replace') as fh:
    cat_src = fh.read()

for name in ['classify_connection_catalog', 'classify_bracing_catalog', 'CatalogEntry', '_probe_family_symbol']:
    if ('def ' + name) not in cat_src and ('class ' + name) not in cat_src:
        errors.append('MISSING SYMBOL in catalog: ' + name)
print('CHECK 4: catalog symbols scan complete')

# ------------------------------------------------------------------
# CHECK 5: Flat lists initialised before populate is defined
# ------------------------------------------------------------------
flat_idx = win_src.find('_flat_beam_beam_items_concrete')
pop_idx  = win_src.find('def _populate_connection_dropdowns')
if flat_idx == -1:
    errors.append('MISSING: _flat_beam_beam_items_concrete not found')
elif pop_idx == -1:
    errors.append('MISSING: _populate_connection_dropdowns not found')
elif flat_idx > pop_idx:
    errors.append('ORDER: flat lists initialised after populate method definition')
else:
    print('CHECK 5: flat list init order OK (flat_idx={}, pop_idx={})'.format(flat_idx, pop_idx))

# ------------------------------------------------------------------
# CHECK 6: Old index getter pattern is gone
# ------------------------------------------------------------------
old_pattern = 'return self._connection_types[idx].element_id'
if old_pattern in win_src:
    errors.append('REGRESSION: old self._connection_types[idx] lookup still present')
else:
    print('CHECK 6: old getter pattern removed OK')

# ------------------------------------------------------------------
# CHECK 7: Header guard present in getter methods
# ------------------------------------------------------------------
if win_src.count('if item is None:') >= 3:
    print('CHECK 7: header guards present (count >= 3)')
else:
    errors.append('MISSING: expected >= 3 header guards (if item is None:)')

# ------------------------------------------------------------------
# CHECK 8: Connection count still uses len(self._connection_types)
# ------------------------------------------------------------------
if 'len(self._connection_types)' in win_src:
    print('CHECK 8: footer count expression present')
else:
    errors.append('MISSING: len(self._connection_types) in footer')

# ------------------------------------------------------------------
# CHECK 9: New imports present
# ------------------------------------------------------------------
for sym in ['classify_connection_catalog', 'classify_bracing_catalog']:
    if sym not in win_src:
        errors.append('MISSING import: ' + sym)
print('CHECK 9: new import scan complete')

# ------------------------------------------------------------------
# CHECK 10: _first_selectable static method exists
# ------------------------------------------------------------------
if '_first_selectable' in win_src:
    print('CHECK 10: _first_selectable present')
else:
    errors.append('MISSING: _first_selectable')

# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------
print()
if errors:
    print('=== FAILURES ===')
    for e in errors:
        print('  FAIL:', e)
    sys.exit(1)
else:
    print('=== ALL 10 STATIC CHECKS PASSED ===')
