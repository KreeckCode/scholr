"""
Results as a spreadsheet.

Built from the same query and grid as the results pages, so an export holds exactly the
participants the person could see on screen, and never anyone outside their reach.

An export can be one sheet, or split into a sheet per partner or per cohort. Every
workbook starts with a Summary sheet and ends with an About sheet saying what was
exported, with which filters, by whom and when.
"""
import re
from collections import OrderedDict

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from partners.models import CourseResult, PartnerOrganisation, TrainingLocation, Cohort, country_name

from . import results

ONE_SHEET, BY_PARTNER, BY_COHORT = 'none', 'partner', 'cohort'
SPLITS = (ONE_SHEET, BY_PARTNER, BY_COHORT)

PERSON_COLUMNS = ['Country', 'Partner', 'Location', 'Cohort', 'Last name', 'First name', 'Status']
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_FILL = PatternFill('solid', fgColor='0F2A44')


def build_workbook(user, view, filters, split=ONE_SHEET):
    view = view if view in results.VIEWS else results.PROGRAMME
    split = split if split in SPLITS else ONE_SHEET
    courses = results.active_courses()
    rows = results.grid(results.visible_memberships(user, **filters), courses)

    workbook = Workbook()
    _write_summary(workbook.active, rows, courses, view)
    if split == ONE_SHEET:
        _write_results(workbook.create_sheet('Results'), rows, courses, view)
    else:
        titles = set()
        for label, group in _grouped(rows, split).items():
            _write_results(workbook.create_sheet(_sheet_title(label, titles)), group, courses, view)
    _write_about(workbook.create_sheet('About'), user, view, filters, split, len(rows))
    return workbook


def filename(view, today=None):
    today = today or timezone.localdate()
    return f'metp-{"online-quiz" if view == results.ONLINE else "programme"}-results-{today:%Y-%m-%d}.xlsx'


# ==================== SHEETS ====================

def _write_results(sheet, rows, courses, view):
    sheet.append(PERSON_COLUMNS + [course.code for course in courses] + ['Courses passed'])
    for row in rows:
        membership = row['membership']
        values, passed = [], 0
        for cell in row['cells']:
            value, cell_passed = _value(cell, view)
            values.append(value)
            passed += cell_passed
        sheet.append(_person(membership) + values + [passed])
    _style(sheet, first_score_column=len(PERSON_COLUMNS) + 1)


def _write_summary(sheet, rows, courses, view):
    sheet.title = 'Summary'
    sheet.append(['Country', 'Partner', 'Location', 'Cohort', 'Participants']
                 + [course.code for course in courses] + ['Passed every course'])
    by_cohort = OrderedDict()
    for row in rows:
        by_cohort.setdefault(row['membership'].cohort_id, []).append(row)
    for group in by_cohort.values():
        membership = group[0]['membership']
        passed_per_course = [
            sum(1 for row in group if _value(row['cells'][index], view)[1]) for index in range(len(courses))
        ]
        passed_all = sum(1 for row in group if all(_value(cell, view)[1] for cell in row['cells']))
        sheet.append(_person(membership)[:4] + [len(group)] + passed_per_course + [passed_all])
    _style(sheet, first_score_column=5)


def _write_about(sheet, user, view, filters, split, count):
    described = {
        'country': country_name(filters.get('country')) if filters.get('country') else '',
        'organisation': _name_of(PartnerOrganisation, filters.get('organisation')),
        'location': _name_of(TrainingLocation, filters.get('location')),
        'cohort': _name_of(Cohort, filters.get('cohort')),
    }
    lines = [
        ('Results', 'Online quiz results' if view == results.ONLINE else 'Programme results'),
        ('Country', described['country'] or 'Every country you can see'),
        ('Partner', described['organisation'] or 'Every partner you can see'),
        ('Location', described['location'] or 'Every location you can see'),
        ('Cohort', described['cohort'] or 'Every cohort you can see'),
        ('Sheets', {ONE_SHEET: 'One sheet', BY_PARTNER: 'One per partner', BY_COHORT: 'One per cohort'}[split]),
        ('Participants', count),
        ('Exported by', user.get_full_name() or user.username),
        ('Exported on', timezone.localtime().strftime('%d %B %Y, %H:%M')),
    ]
    if view == results.ONLINE:
        lines.append(('Scores', 'Percentage of all the quiz points in the course, from the LMS'))
    else:
        lines.append(('Scores', 'Exam score where recorded, otherwise the course status'))
    for label, value in lines:
        sheet.append([label, value])
    sheet.column_dimensions['A'].width = 16
    sheet.column_dimensions['B'].width = 60
    for cell in sheet['A']:
        cell.font = Font(bold=True)


# ==================== HELPERS ====================

def _person(membership):
    location = membership.cohort.location
    return [
        country_name(location.organisation.country), location.organisation.name, location.name,
        membership.cohort.name, membership.participant.last_name, membership.participant.first_name,
        membership.get_status_display(),
    ]


def _value(cell, view):
    """(what to show for one course, whether it counts as passed)."""
    if view == results.ONLINE:
        online = cell['online']
        if online is None:
            return '', False
        return online.overall_percentage, bool(online.passed)
    result = cell['programme']
    if result is None:
        return '', False
    passed = result.status == CourseResult.Status.PASSED
    if result.exam_score is not None:
        return float(result.exam_score), passed
    return result.get_status_display(), passed


def _grouped(rows, split):
    groups = OrderedDict()
    for row in rows:
        cohort = row['membership'].cohort
        label = cohort.location.organisation.name if split == BY_PARTNER else f'{cohort.name} {cohort.location.name}'
        groups.setdefault(label, []).append(row)
    return groups


def _sheet_title(label, taken):
    """Excel allows 31 characters and no []:*?/\\ in a sheet name, and names must be unique."""
    base = re.sub(r'[\[\]:*?/\\]', ' ', label).strip()[:28] or 'Sheet'
    title, number = base, 2
    while title.lower() in taken or title.lower() in ('summary', 'about'):
        title = f'{base[:25]} ({number})'
        number += 1
    taken.add(title.lower())
    return title


def _name_of(model, public_id):
    if not public_id:
        return ''
    found = model.objects.filter(public_id=public_id).values_list('name', flat=True).first()
    return found or ''


def _style(sheet, first_score_column):
    for cell in sheet[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    sheet.freeze_panes = sheet.cell(row=2, column=first_score_column)
    if sheet.max_row > 1:
        sheet.auto_filter.ref = sheet.dimensions
    for index, column in enumerate(sheet.iter_cols(min_row=1, max_row=min(sheet.max_row, 200)), start=1):
        longest = max((len(str(cell.value)) for cell in column if cell.value is not None), default=8)
        sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 9), 40)
