"""One-off maintenance of the data imported with the Ki-nTree rules.

Run inside the InvenTree container, e.g.:

    docker compose exec -w /home/inventree/src/backend/InvenTree inventree-server python manage.py shell \\
        -c "from supplier_part_import.maintenance import move_inductor_dcr; move_inductor_dcr()"

Without apply=True nothing is written: the functions only show what would change.
"""

from django.db import transaction


def move_inductor_dcr(apply: bool = False, category: str = 'Inductors', scope: str = 'Inductors',
                      old: str = 'ESR', new: str = 'DC Resistance', default_units: str = 'mΩ') -> dict:
    """Move the DC resistance of the inductors from the 'ESR' template to 'DC Resistance'.

    Ki-nTree mapped the DCR of the inductors to ESR:
    1. the DCR names of the 'Inductors' parameter scope move from 'ESR' to 'DC Resistance'
    2. the 'DC Resistance' template is created if missing (units from the plugin units table)
    3. the ESR values of the parts in the 'Inductors' category (and subcategories) move to 'DC Resistance'

    Parts which already have 'DC Resistance' are not changed (reported).
    """
    from django.contrib.contenttypes.models import ContentType

    from common.models import Parameter, ParameterTemplate
    from part.models import Part, PartCategory

    from .models import ParameterScope, TemplateUnit
    from .units import to_template_units

    summary = {'scope': '', 'template': '', 'moved': 0, 'skipped': 0, 'failed': 0}
    print(f'--- {"APPLY" if apply else "DRY RUN: nothing is written (apply=True to apply)"}')

    with transaction.atomic():
        # 1. Plugin mappings
        parameter_scope = ParameterScope.objects.filter(name=scope).first()

        if parameter_scope is None:
            summary['scope'] = 'not found'
        elif old not in (parameter_scope.mappings or {}):
            summary['scope'] = f'no "{old}" mapping (already moved)'
        else:
            names = parameter_scope.mappings.pop(old)
            target = parameter_scope.mappings.setdefault(new, [])
            target += [n for n in names if n not in target]
            parameter_scope.save()
            summary['scope'] = f'{names} moved from "{old}" to "{new}"'

        print(f'Scope "{scope}": {summary["scope"]}')

        # 2. Template
        unit = TemplateUnit.objects.filter(name__iexact=new).first()
        new_template = ParameterTemplate.objects.filter(name__iexact=new).first()

        if new_template is None:
            units = unit.units if unit and unit.units else default_units
            new_template = ParameterTemplate.objects.create(name=new, units=units)
            summary['template'] = f'created [{units}]'
        else:
            summary['template'] = f'exists [{new_template.units}]'

        print(f'Template "{new}": {summary["template"]}')

        # 3. Values of the inductors
        old_template = ParameterTemplate.objects.filter(name__iexact=old).first()
        root = PartCategory.objects.filter(name=category, parent=None).first()

        if root is None or old_template is None:
            print(f'Category "{category}" or template "{old}" not found: no values to move')
        else:
            content_type = ContentType.objects.get_for_model(Part)
            parts = Part.objects.filter(category__in=root.get_descendants(include_self=True))
            names = dict(parts.values_list('pk', 'name'))
            parameters = Parameter.objects.filter(
                model_type=content_type, model_id__in=list(names), template=old_template
            )

            for parameter in parameters:
                name = names.get(parameter.model_id, parameter.model_id)

                if Parameter.objects.filter(
                    model_type=content_type, model_id=parameter.model_id, template=new_template
                ).exists():
                    print(f'  {name}: already has "{new}", "{old}" = {parameter.data} left unchanged')
                    summary['skipped'] += 1
                    continue

                value = parameter.data

                if new_template.units and old_template.units != new_template.units:
                    converted = to_template_units(f'{value}{old_template.units}', new_template.units)

                    if converted is None:
                        print(f'  {name}: "{value}" not convertible from {old_template.units} to {new_template.units}')
                        summary['failed'] += 1
                        continue

                    value = converted

                print(f'  {name}: {old} {parameter.data} -> {new} {value}')

                # Same row with the new template: the parameter keeps its history
                parameter.template = new_template
                parameter.data = value
                parameter.save()
                summary['moved'] += 1

            print(f'Values: {summary["moved"]} moved, {summary["skipped"]} skipped (already set), '
                  f'{summary["failed"]} not convertible')

        if not apply:
            transaction.set_rollback(True)

    print('--- done' if apply else '--- dry run: rolled back')
    return summary
