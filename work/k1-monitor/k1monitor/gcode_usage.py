"""Read-only, bounded material estimates for ordinary single-material text G-code.

No G-code from this module is executed. Retractions and G92 resets do not
consume filament. Layer/M73 curves are extrusion curves, not elapsed-time ratios.
"""
import bisect
import math
import re

MAX_LINES = 12_000_000
MAX_LAYERS = 100_000
VALUE = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
PARAM = re.compile(r'([A-Z])\s*(' + VALUE + r')', re.I)
NUMBERS = re.compile(VALUE)


class GcodeUsageError(ValueError):
    """Unsupported/invalid G-code will not improve with network retries."""


def finite(value, minimum=0, maximum=1_000_000):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def values(text):
    result = [finite(value) for value in NUMBERS.findall(text)]
    return result if result and all(value is not None for value in result) else []


def parse_gcode(file, density=1.24, diameter=1.75):
    """Return a compact JSON profile; file is a binary/text line iterator.

    Settings are explicitly approximate fallbacks when a slicer omitted density
    and diameter. Multi-material, volumetric/inch and binary files are rejected.
    """
    density = finite(density, .5, 3)
    diameter = finite(diameter, 1, 4)
    if density is None or diameter is None:
        raise GcodeUsageError('Некорректные параметры филамента')
    metadata = {}
    position = {}; debt = {}; extruded = {}; tool = 0
    relative = False; explicit_e_mode = False; flow = 1.
    used = 0.; layers = []; progress = {}; previous_progress = -1
    valid_progress = True; layer_marker = None; layer_number = 0
    sequential = False; warnings = set(); unsupported = None
    for count, raw in enumerate(file, 1):
        if count > MAX_LINES or len(raw) > 1024 * 1024:
            raise GcodeUsageError('Слишком сложный G-code для анализа расхода')
        if isinstance(raw, bytes):
            if b'\0' in raw:
                raise GcodeUsageError('Нужен текстовый G-code')
            line = raw.decode('utf-8', errors='replace').strip()
        else:
            line = raw.strip()
        if not line:
            continue
        if line.startswith(';'):
            comment = line[1:].strip(); low = comment.lower()
            # Config/footer variants from CP/Orca/Bambu/Prusa, Cura and S3D.
            if 'filament' in low or 'material' in low:
                match = re.match(r'(.*?)\s*[:=,]\s*(.*)$', low)
                if match:
                    key, value = match.groups(); nums = values(value)
                    if nums:
                        if key in ('total filament used [g]', 'filament used [g]', 'filament weight', 'filament weight [g]', 'filament_weight'):
                            metadata['total_g' if key.startswith('total ') else 'grams'] = nums
                        elif key in ('filament used [mm]', 'filament length', 'filament_length', 'filament used'):
                            multiplier = 1000 if re.search(r'\d\s*m(?:\s|$)', value) and 'mm' not in value else 1
                            metadata['length'] = [number * multiplier for number in nums]
                        elif key in ('filament used [cm3]', 'filament volume'):
                            metadata['volume'] = nums
                        elif key in ('filament_density', 'filament density', 'filamentdensity', 'material density'):
                            metadata['density'] = nums
                        elif key in ('filament_diameter', 'filament diameter', 'filamentdiameter', 'material diameter'):
                            metadata['diameter'] = nums
            if re.match(r'(?:print_sequence|complete_objects)\s*[:=]\s*(?:by object|1)', low):
                sequential = True
            marker = None
            # BEFORE/AFTER_LAYER_CHANGE are additional tags, not new layers.
            if low == 'layer_change' or low.startswith('change_layer'):
                marker = 'change'
            elif re.match(r'layer\s*:\s*\d+', low):
                marker = 'indexed'
            elif re.match(r'layer\s+\d+(?:\s|,|$)', low):
                marker = 'indexed'
            if marker is not None:
                if layer_marker is None:
                    layer_marker = marker
                if marker == layer_marker:
                    layer_number += 1
                    if layer_number > MAX_LAYERS:
                        raise GcodeUsageError('Слишком много слоёв для анализа расхода')
                    layers.append([layer_number, used])
            continue
        code = line.split(';', 1)[0].split('*', 1)[0].strip().upper()
        code = re.sub(r'^N\d+\s*', '', code)
        match = re.match(r'^([GMT])\s*(\d+)(?:\.\d+)?', code)
        if not match:
            if code.startswith(('START_PRINT', 'PRINT_START', 'END_PRINT', 'PRINT_END')):
                warnings.add('macro')
            continue
        command = match[1] + str(int(match[2]))
        if command == 'M83':
            relative = True; explicit_e_mode = True
        elif command == 'M82':
            relative = False; explicit_e_mode = True
        elif command in ('G90', 'G91') and not explicit_e_mode:
            relative = command == 'G91'
        elif command == 'G20':
            unsupported = 'Дюймовый G-code не поддерживается для учёта катушки'
        elif command.startswith('T'):
            tool = int(match[2])
        elif command in ('G0', 'G1', 'G2', 'G3', 'G92', 'M73', 'M200', 'M221'):
            params = {key: float(value) for key, value in PARAM.findall(code[match.end():])}
            if any(not math.isfinite(value) for value in params.values()):
                raise GcodeUsageError('Некорректные числа в G-code')
            if command == 'M200' and params.get('D', 0) > 0:
                unsupported = 'Объёмная экструзия не поддерживается для учёта катушки'
            elif command == 'M221' and 'S' in params:
                flow = params['S'] / 100
                if not 0 <= flow <= 5:
                    raise GcodeUsageError('Некорректный коэффициент подачи в G-code')
            elif command == 'M73' and 'P' in params:
                point = params['P']
                if 0 <= point <= 100:
                    if point < previous_progress:
                        valid_progress = False
                    # The first command for the NEXT percent is the upper bound.
                    progress.setdefault(str(int(point)), used)
                    previous_progress = point
            elif command == 'G92' and 'E' in params:
                position[tool] = params['E']
            elif command.startswith('G') and 'E' in params:
                value = params['E']; old = position.get(tool, 0)
                delta = value if relative else value - old
                position[tool] = old + value if relative else value
                if abs(delta) > 1_000_000:
                    raise GcodeUsageError('Некорректная подача пластика в G-code')
                if delta < 0:
                    debt[tool] = debt.get(tool, 0) - delta
                else:
                    restored = min(debt.get(tool, 0), delta)
                    debt[tool] = debt.get(tool, 0) - restored
                    amount = (delta - restored) * flow
                    used += amount
                    extruded[tool] = extruded.get(tool, 0) + amount
    if unsupported:
        raise GcodeUsageError(unsupported)
    per_tool = metadata.get('grams') or metadata.get('length') or []
    if sum(value > .001 for value in extruded.values()) > 1 or sum(value > .001 for value in per_tool) > 1:
        raise GcodeUsageError('Несколько материалов: автоматический учёт одной катушки недоступен')
    meta_density = (metadata.get('density') or [None])[0]
    meta_diameter = (metadata.get('diameter') or [None])[0]
    if finite(meta_density, .5, 3) is not None:
        density = meta_density
    if finite(meta_diameter, 1, 4) is not None:
        diameter = meta_diameter
    mass = sum(metadata.get('total_g') or metadata.get('grams') or [])
    length = sum(metadata.get('length') or [])
    grams_per_mm = mass / length if mass > 0 and length > 0 else math.pi * (diameter / 2) ** 2 * density / 1000
    parsed_g = used * grams_per_mm
    if not mass and metadata.get('volume'):
        mass = sum(metadata['volume']) * density
    if not mass and length:
        mass = length * grams_per_mm
    total = max(mass, parsed_g)
    if not 0 < total <= 100_000:
        raise GcodeUsageError('Не удалось определить расход пластика из G-code')
    # Scale the extrusion curve to the chosen conservative total estimate.
    factor = total / used if used > 0 else 0
    layer_bounds = [[index, amount * factor] for index, amount in layers]
    if layers and not sequential:
        layer_bounds.append([layer_number + 1, total])
    else:
        layer_bounds = []
    points = sorted((int(key), amount * factor) for key, amount in progress.items()) if valid_progress and used > 0 else []
    if points:
        if points[0][0] != 0:
            points.insert(0, (0, 0.))
        points.append((101, total))
    return {'total_g': total, 'length_mm': used or length or None,
            'density': density, 'diameter': diameter,
            'source': 'slicer' if mass else 'extrusion',
            'assumed_density': meta_density is None and not (mass and length),
            'layers': layer_bounds, 'layer_count': layer_number,
            'progress': points, 'warnings': sorted(warnings)}


def consumption(profile, details, completed=False):
    """(lower, upper, method). Reserve a whole job if its position is unknown."""
    total = profile['total_g']
    if completed:
        return total, total, 'complete'
    layer = finite(details.get('layer'), 1, MAX_LAYERS)
    reported_total = finite(details.get('total_layers'), 1, MAX_LAYERS)
    bounds = profile.get('layers', [])
    if bounds and layer is not None and layer.is_integer() and (reported_total is None or reported_total == profile['layer_count']):
        index = int(layer) - 1
        if index < len(bounds) - 1:
            return bounds[index][1], bounds[index + 1][1], 'layers'
    point = finite(details.get('progress_percent'), 0, 100)
    points = profile.get('progress', [])
    if points and point is not None:
        index = max(0, bisect.bisect_right([item[0] for item in points], point) - 1)
        if index < len(points) - 1:
            return points[index][1], points[index + 1][1], 'gcode_progress'
    return 0., total, 'reservation'
