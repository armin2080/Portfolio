"""Tests for the CV generated from the site's own content.

The interesting cases here are not "does it find the data" but the two ways a
generated document can go wrong quietly:

* **Escaping.** Every value ends up inside a LaTeX document, so a skill called
  ``C++`` or a description containing ``50%`` can break the compile — and a
  description containing ``\\end{document}`` could try to inject LaTeX. Values
  are escaped in ``build_resume_context``, and these tests treat that as the
  contract: if escaping is ever dropped, the document either fails to compile or
  the injected text survives into the PDF.
* **Leaking template syntax.** The design file is a Jinja2 template, so a marker
  that fails to parse would be printed verbatim into the CV. Hand-written
  documents have leaked their own source onto the live site in this project
  before (see the ``{`` ``#`` comments in templates), so the rendered output is
  checked for leftovers.

Compiling is tested only when a LaTeX engine is actually installed — CI has none,
and the render/escape behaviour that matters most needs no engine at all.
"""

from datetime import date
from unittest import mock, skipUnless

from django.core.cache import cache
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from . import resume_latex
from .models import (
    Certificate,
    Education,
    PageView,
    Profile,
    Project,
    Skill,
    WorkExperience,
)

LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'resume-tests',
    }
}


def compiled(pdf=b'%PDF-1.5', pages=1):
    """A stand-in for a real compile, so mocking one reads clearly."""
    return resume_latex.Compilation(pdf=pdf, pages=pages)


def make_profile(**overrides):
    """Create the single profile row, replacing any the test database seeded."""
    Profile.objects.all().delete()
    fields = {
        'name': 'Armin Maddah Asl',
        'title': 'Data Scientist',
        'email': 'armin@example.com',
        'phone': '+49 123',
        'location': 'Dortmund, Germany',
        'linkedin_url': 'https://linkedin.com/in/armin',
        'github_url': 'https://github.com/armin2080',
        'professional_summary': 'Data scientist working on machine learning.',
    }
    fields.update(overrides)
    return Profile.objects.create(**fields)


class LatexEscapingTests(TestCase):
    """The escaper is the one thing standing between the data and the engine."""

    def test_every_special_character_is_escaped(self):
        self.assertEqual(
            resume_latex.latex_escape(r'\ & % $ # _ { }'),
            r'\textbackslash{} \& \% \$ \# \_ \{ \}',
        )

    def test_tilde_and_caret_are_escaped(self):
        self.assertEqual(
            resume_latex.latex_escape('a~b^c'),
            r'a\textasciitilde{}b\textasciicircum{}c',
        )

    def test_escaping_happens_in_one_pass(self):
        # The replacement text contains backslashes and braces. Escaping those
        # too would produce \\textbackslash\\{\\} and a document that will not
        # compile, so this is a real regression guard, not a formality.
        self.assertEqual(
            resume_latex.latex_escape(r'\&'),
            r'\textbackslash{}\&',
        )

    def test_none_becomes_empty(self):
        self.assertEqual(resume_latex.latex_escape(None), '')

    def test_ordinary_text_and_unicode_are_untouched(self):
        self.assertEqual(resume_latex.latex_escape('Düsseldorf — 3 days'), 'Düsseldorf — 3 days')

    def test_urls_keep_characters_hyperref_handles_itself(self):
        # hyperref copes with a raw underscore and tilde, and escaping them
        # would corrupt the link; percent and hash are what must be escaped.
        self.assertEqual(
            resume_latex.latex_escape_url('https://example.com/a_b~c'),
            'https://example.com/a_b~c',
        )
        self.assertEqual(
            resume_latex.latex_escape_url('https://example.com/a%20b#c'),
            r'https://example.com/a\%20b\#c',
        )


class DateFormattingTests(TestCase):
    def test_month_year_is_zero_padded(self):
        self.assertEqual(resume_latex.format_month_year(date(2024, 3, 5)), '03/2024')

    def test_missing_date_falls_back_to_present(self):
        self.assertEqual(resume_latex.format_month_year(None), 'Present')

    def test_open_ended_range(self):
        self.assertEqual(
            resume_latex.format_date_range(date(2023, 9, 1), None),
            '09/2023 -- Present',
        )

    def test_closed_range(self):
        self.assertEqual(
            resume_latex.format_date_range(date(2021, 10, 1), date(2023, 7, 1)),
            '10/2021 -- 07/2023',
        )


class DescriptionLineTests(TestCase):
    """Descriptions are shown on the site as bullet lists."""

    def test_lines_are_escaped(self):
        self.assertEqual(
            resume_latex.description_lines('50% faster'),
            [r'50\% faster'],
        )

    def test_blank_lines_are_dropped(self):
        self.assertEqual(
            resume_latex.description_lines('one\n\n\ntwo'),
            ['one', 'two'],
        )

    def test_typed_bullets_are_not_doubled(self):
        # GitHub descriptions and pasted notes often start with a dash; the CV
        # supplies its own bullet, so the typed one has to go.
        self.assertEqual(
            resume_latex.description_lines('- first\n* second\n• third'),
            ['first', 'second', 'third'],
        )

    def test_empty_description_yields_no_lines(self):
        self.assertEqual(resume_latex.description_lines(''), [])
        self.assertEqual(resume_latex.description_lines(None), [])


class ResumeContextTests(TestCase):
    def setUp(self):
        # The tree is seeded by a data migration, so tests must clear it before
        # creating their own skills or they collide on the unique names.
        Skill.objects.all().delete()
        Project.objects.all().delete()
        Education.objects.all().delete()
        WorkExperience.objects.all().delete()
        Certificate.objects.all().delete()
        make_profile()

    def test_header_comes_from_the_profile(self):
        context = resume_latex.build_resume_context()
        self.assertEqual(context['name'], 'Armin Maddah Asl')
        # The eyebrow line is upper-cased in the template.
        self.assertEqual(context['title'], 'DATA SCIENTIST')
        self.assertEqual(context['summary'], 'Data scientist working on machine learning.')

    def test_contact_entries_link_what_can_be_linked(self):
        entries = {entry['label']: entry['url'] for entry in resume_latex.build_resume_context()['contact']}
        self.assertEqual(entries['armin@example.com'], 'mailto:armin@example.com')
        self.assertEqual(entries['LinkedIn'], 'https://linkedin.com/in/armin')
        self.assertEqual(entries['GitHub'], 'https://github.com/armin2080')
        # Plain text entries carry no URL, so the template renders them as text.
        self.assertEqual(entries['Dortmund, Germany'], '')
        self.assertEqual(entries['+49 123'], '')

    def test_profile_values_are_escaped(self):
        make_profile(name='Armin & Co', title='R&D Lead', location='100% remote')
        context = resume_latex.build_resume_context()
        self.assertEqual(context['name'], r'Armin \& Co')
        self.assertEqual(context['title'], r'R\&D LEAD')
        labels = [entry['label'] for entry in context['contact']]
        self.assertIn(r'100\% remote', labels)

    def test_a_missing_profile_does_not_break_generation(self):
        Profile.objects.all().delete()
        context = resume_latex.build_resume_context()
        self.assertEqual(context['name'], '')
        self.assertEqual(context['summary'], '')
        # Nothing profile-derived survives, but the site's own link does — it is
        # worth having on the CV even before the profile has been filled in.
        self.assertEqual([entry['label'] for entry in context['contact']], ['Portfolio'])
        self.assertIn(r'\end{document}', resume_latex.build_latex_source())

    def test_empty_contact_fields_leave_no_stray_separators(self):
        # A profile with only a name must produce exactly one contact entry. A
        # link whose URL is built from the field (mailto:) is the trap: the
        # empty case yields the truthy string "mailto:", which added an entry
        # with no label and left a dangling "|" in the header.
        make_profile(email='', phone='', location='', linkedin_url='', github_url='')
        entries = resume_latex.build_resume_context()['contact']
        self.assertEqual(entries, [{'label': 'Portfolio', 'url': 'https://armin2080.de'}])
        # And the rendered header shows no lone separator.
        self.assertNotIn(r'\;|\; \\resumelink', resume_latex.build_latex_source())

    def test_experience_dates_and_bullets(self):
        WorkExperience.objects.create(
            role='Working Student', company='ACME', location='Düsseldorf',
            description='Did things\nGot 20% better',
            start_date=date(2023, 1, 1), end_date=None,
        )
        entry = resume_latex.build_resume_context()['experiences'][0]
        self.assertEqual(entry['company'], 'ACME')
        self.assertEqual(entry['dates'], '01/2023 -- Present')
        self.assertEqual(entry['bullets'], ['Did things', r'Got 20\% better'])

    def test_education_without_an_end_date_reads_as_present(self):
        Education.objects.create(
            degree='M.Sc. Data Science', institution='TU Dortmund',
            start_date=date(2022, 10, 1), end_date=None,
        )
        entry = resume_latex.build_resume_context()['educations'][0]
        self.assertEqual(entry['dates'], '10/2022 -- Present')

    def test_sub_skills_become_the_detail_line(self):
        python = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))
        # display_order is what the site uses to order sub-skills, so the test
        # sets it rather than relying on the start dates.
        Skill.objects.create(
            name='FastAPI', start_date=date(2021, 1, 1), parent=python, display_order=1,
        )
        Skill.objects.create(
            name='NumPy', start_date=date(2020, 1, 1), parent=python, display_order=2,
        )
        skills = resume_latex.build_resume_context()['skills']
        self.assertEqual(skills, [{'name': 'Python', 'technologies': 'FastAPI, NumPy'}])

    def test_main_skill_without_sub_skills_has_no_detail_line(self):
        Skill.objects.create(name='Linux', start_date=date(2018, 1, 1))
        self.assertEqual(
            resume_latex.build_resume_context()['skills'],
            [{'name': 'Linux', 'technologies': ''}],
        )

    def test_unpublished_skills_are_left_off_the_cv(self):
        python = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))
        Skill.objects.create(
            name='Secret', start_date=date(2020, 1, 1), parent=python, is_published=False,
        )
        Skill.objects.create(name='Hidden', start_date=date(2020, 1, 1), is_published=False)
        self.assertEqual(
            resume_latex.build_resume_context()['skills'],
            [{'name': 'Python', 'technologies': ''}],
        )

    def test_keywords_cover_main_and_sub_skills(self):
        python = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))
        Skill.objects.create(name='FastAPI', start_date=date(2021, 1, 1), parent=python)
        self.assertEqual(resume_latex.build_resume_context()['keywords'], 'Python, FastAPI')

    def test_projects_are_limited_and_only_published(self):
        for number in range(5):
            Project.objects.create(
                name=f'Project {number}', link=f'https://example.com/{number}',
                date=date(2024, 1, 1 + number),
            )
        Project.objects.create(
            name='Hidden', link='https://example.com/hidden',
            date=date(2025, 1, 1), is_published=False,
        )
        with override_settings(RESUME_MAX_PROJECTS=2):
            names = [p['name'] for p in resume_latex.build_resume_context()['projects']]
        # Newest first, and never the unpublished one.
        self.assertEqual(names, ['Project 4', 'Project 3'])

    def test_project_technologies_roll_up_to_main_skills(self):
        python = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))
        sub = Skill.objects.create(name='FastAPI', start_date=date(2021, 1, 1), parent=python)
        project = Project.objects.create(name='API', link='https://example.com/api')
        project.skills_used.add(sub)
        entry = resume_latex.build_resume_context()['projects'][0]
        self.assertEqual(entry['technologies'], 'Python')

    def test_certificates_carry_year_and_issuer(self):
        Certificate.objects.create(
            name='Machine Learning', institution='Coursera', issue_date=date(2022, 5, 1),
        )
        entry = resume_latex.build_resume_context()['certificates'][0]
        self.assertEqual(entry, {'name': 'Machine Learning', 'institution': 'Coursera', 'year': 2022})

    def test_certificates_are_capped_to_the_most_recent(self):
        # This site has several dozen certificates. Listing all of them turned
        # the CV into an appendix, so the list is capped the way projects are.
        for year in (2019, 2020, 2021):
            Certificate.objects.create(
                name=f'Cert {year}', institution='Issuer', issue_date=date(year, 1, 1),
            )
        with override_settings(RESUME_MAX_CERTIFICATES=2):
            names = [item['name'] for item in resume_latex.build_resume_context()['certificates']]
        self.assertEqual(names, ['Cert 2021', 'Cert 2020'])

    def test_languages_come_from_the_constant(self):
        languages = resume_latex.build_resume_context()['languages']
        self.assertEqual([item['name'] for item in languages], ['Persian', 'English', 'German'])


class ResumeTemplateTests(TestCase):
    """The rendered document, which is what actually reaches the engine."""

    def setUp(self):
        Skill.objects.all().delete()
        Project.objects.all().delete()
        Education.objects.all().delete()
        WorkExperience.objects.all().delete()
        Certificate.objects.all().delete()
        make_profile()
        self.source = resume_latex.build_latex_source()

    def test_renders_a_complete_document(self):
        self.assertIn(r'\documentclass', self.source)
        self.assertIn(r'\begin{document}', self.source)
        self.assertIn(r'\end{document}', self.source)
        self.assertTrue(self.source.rstrip().endswith(r'\end{document}'))

    def test_no_template_markers_survive_into_the_document(self):
        # A tag that failed to parse would be printed as literal text, which in
        # LaTeX means a visible artefact in the PDF (or a compile error).
        for marker in ('<<', '>>', '<%', '%>', '<#', '#>'):
            self.assertNotIn(marker, self.source, f'{marker} leaked into the document')

    def test_data_cannot_inject_latex(self):
        # The only \end{document} should be the real one.
        make_profile(name=r'Armin \end{document} Maddah')
        Skill.objects.create(name=r'\input{/etc/passwd}', start_date=date(2020, 1, 1))
        source = resume_latex.build_latex_source()
        self.assertEqual(source.count(r'\end{document}'), 1)
        self.assertIn(r'\textbackslash{}end\{document\}', source)
        self.assertNotIn(r'\input{/etc/passwd}', source)

    def test_percent_in_content_does_not_comment_out_the_line(self):
        make_profile(professional_summary='Improved throughput 50% faster')
        self.assertIn(r'Improved throughput 50\% faster', resume_latex.build_latex_source())

    def test_empty_sections_are_omitted_entirely(self):
        # No experience rows exist in this test, so neither the heading nor its
        # list environment should appear.
        self.assertNotIn(r'\section{Experience}', self.source)
        self.assertNotIn(r'\section{Education}', self.source)
        self.assertNotIn(r'\section{Selected Projects}', self.source)

    def test_present_sections_are_rendered(self):
        WorkExperience.objects.create(
            role='Working Student', company='ACME', location='Düsseldorf',
            description='Built things', start_date=date(2023, 1, 1),
        )
        source = resume_latex.build_latex_source()
        self.assertIn(r'\section{Experience}', source)
        self.assertIn(r'{ACME}', source)
        self.assertIn(r'\resumeItem{Built things}', source)

    def test_languages_and_certificates_share_the_last_section(self):
        source = resume_latex.build_latex_source()
        self.assertIn(r'\section{Additional Information}', source)
        self.assertIn(r'\textbf{Languages:}', source)
        self.assertIn('Persian (Native), English (Advanced (C1)), German (A2/B1)', source)

    def test_the_certification_list_is_compact_and_newest_first(self):
        # Capped to a handful, the certifications read better as one line than as
        # a bulleted list: with four bullets the section dominated half a page.
        for year in (2021, 2022, 2023):
            Certificate.objects.create(
                name=f'Cert {year}', institution='Coursera', issue_date=date(year, 5, 1),
            )
        source = resume_latex.build_latex_source()
        self.assertIn(r'\textbf{Certifications:}', source)
        self.assertIn('Cert 2023 (Coursera, 2023); Cert 2022 (Coursera, 2022)', source)
        # Newest first, and no per-certificate bullet.
        self.assertLess(source.index('Cert 2023'), source.index('Cert 2021'))
        self.assertNotIn(r'\resumeItem{Cert 2023', source)

    def test_template_documents_its_delimiters_without_leaking_them(self):
        # The header explains the delimiters. It does so inside a comment, so it
        # reaches whoever edits the design but never the generated document —
        # otherwise every CV would carry a paragraph of template instructions.
        template = (
            resume_latex.RESUME_DIR / resume_latex.RESUME_TEMPLATE_NAME
        ).read_text()
        self.assertIn('<< value >>', template)
        self.assertNotIn('<< value >>', self.source)


class EngineDetectionTests(TestCase):
    def test_an_explicit_binary_path_wins(self):
        with mock.patch.object(resume_latex.shutil, 'which', return_value='/usr/bin/pdflatex'), \
                mock.patch.object(resume_latex.os, 'access', return_value=True), \
                mock.patch.object(resume_latex.Path, 'is_file', return_value=True):
            engine = resume_latex.find_engine()
        # With nothing configured the local Tectonic binary is preferred, and
        # the mocked filesystem makes it look present.
        self.assertEqual(engine.name, 'tectonic')

    @override_settings(RESUME_LATEX_ENGINE='/opt/tex/pdflatex')
    def test_resume_latex_engine_overrides_detection(self):
        with mock.patch.object(resume_latex.os, 'access', return_value=True), \
                mock.patch.object(resume_latex.Path, 'is_file', return_value=True):
            engine = resume_latex.find_engine()
        self.assertEqual(engine.name, 'pdflatex')
        self.assertEqual(engine.path, '/opt/tex/pdflatex')

    @override_settings(RESUME_LATEX_ENGINE='/nope/not-a-binary')
    def test_a_configured_path_that_is_missing_falls_back_to_detection(self):
        with mock.patch.object(resume_latex, '_SYSTEM_ENGINES', ()):
            with mock.patch.object(resume_latex.shutil, 'which', return_value=None), \
                    override_settings(BASE_DIR='/nonexistent-base-dir'):
                self.assertIsNone(resume_latex.find_engine())
                self.assertFalse(resume_latex.engine_available())

    def test_tectonic_command_disables_shell_escape(self):
        engine = resume_latex.LatexEngine(name='tectonic', path='/bin/tectonic')
        command = engine.command('resume.tex', '/tmp/out')
        self.assertIn('--untrusted', command)

    def test_pdflatex_command_never_stops_for_input(self):
        engine = resume_latex.LatexEngine(name='pdflatex', path='/bin/pdflatex')
        command = engine.command('resume.tex', '/tmp/out')
        self.assertIn('-interaction=nonstopmode', command)
        self.assertIn('-halt-on-error', command)
        # Do not rely on the distribution's default for the one place the site
        # runs an external program over database content.
        self.assertIn('-no-shell-escape', command)


@override_settings(CACHES=LOCMEM)
class CompilePipelineTests(TestCase):
    """Compiling and caching, with the engine itself mocked out."""

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        make_profile()
        # CI has no LaTeX engine. These tests mock the compile step, so the
        # engine only needs to be discoverable; without this the common
        # pipeline tests would fail before reaching the code under test.
        engine = resume_latex.LatexEngine(name='tectonic', path='/bin/tectonic')
        patcher = mock.patch.object(resume_latex, 'find_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_missing_engine_is_reported_as_such(self):
        with mock.patch.object(resume_latex, 'find_engine', return_value=None):
            with self.assertRaises(resume_latex.LatexEngineMissing):
                resume_latex.generate_pdf()

    def test_the_pdf_is_compiled_once_and_then_cached(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()) as compile_mock:
            first = resume_latex.generate_pdf()
            second = resume_latex.generate_pdf()
        self.assertEqual(first, second)
        self.assertEqual(compile_mock.call_count, 1)

    def test_force_recompiles_and_refreshes_the_cache(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled(b'old')) as compile_mock:
            resume_latex.generate_pdf()
            with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled(b'new')) as second:
                self.assertEqual(resume_latex.generate_pdf(force=True).pdf, b'new')
        self.assertEqual(compile_mock.call_count, 1)
        self.assertEqual(second.call_count, 1)

    def test_the_cache_key_follows_the_content(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()) as compile_mock:
            resume_latex.generate_pdf()
            # Changing the site changes the LaTeX, which must produce a different
            # key — otherwise an edited CV would serve the previous PDF.
            make_profile(name='Someone Else')
            resume_latex.generate_pdf()
        self.assertEqual(compile_mock.call_count, 2)

    def test_the_cache_key_follows_the_selection(self):
        # The generator's whole purpose is to vary the content, so a different
        # selection must not be answered from another selection's cache entry.
        # Two skills are needed for the selections to differ at all.
        python = Skill.objects.create(
            name='Python', start_date=date(2019, 1, 1), display_order=1,
        )
        Skill.objects.create(name='Linux', start_date=date(2018, 1, 1), display_order=2)
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()) as compile_mock:
            resume_latex.generate_pdf()
            resume_latex.generate_pdf(resume_latex.ResumeOptions(skill_ids=(python.pk,)))
        self.assertEqual(compile_mock.call_count, 2)

    def test_caching_can_be_turned_off(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()) as compile_mock:
            resume_latex.generate_pdf(use_cache=False)
            resume_latex.generate_pdf(use_cache=False)
        self.assertEqual(compile_mock.call_count, 2)

    def test_the_compile_lock_is_released_afterwards(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()):
            resume_latex.generate_pdf()
        self.assertFalse(resume_latex._compile_lock_is_held())

    def test_only_one_process_can_hold_the_compile_lock(self):
        first = resume_latex._acquire_compile_lock()
        self.assertIsNotNone(first)
        try:
            self.assertTrue(resume_latex._compile_lock_is_held())
            self.assertIsNone(resume_latex._acquire_compile_lock())
        finally:
            resume_latex._release_compile_lock(first)
        self.assertFalse(resume_latex._compile_lock_is_held())

    def test_the_page_count_is_cached_with_the_pdf(self):
        # The count is part of the cached entry, so a cache hit does not have to
        # recompile (or re-read a log) to know the document's length.
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled(pages=2)) as compile_mock:
            self.assertEqual(resume_latex.generate_pdf().pages, 2)
            self.assertEqual(resume_latex.generate_pdf().pages, 2)
        self.assertEqual(compile_mock.call_count, 1)

    def test_a_two_page_cv_is_logged_as_a_warning(self):
        # Not an error — the PDF is still produced — but the one-page promise has
        # been broken and the owner should hear about it before a visitor does.
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled(pages=2)):
            with self.assertLogs('portfolio_app.resume_latex', level='WARNING') as caught:
                resume_latex.generate_pdf()
        self.assertIn('2 pages', ' '.join(caught.output))

    def test_the_lock_is_released_even_when_the_compile_fails(self):
        error = resume_latex.LatexCompileError('boom')
        with mock.patch.object(resume_latex, 'compile_latex', side_effect=error):
            with self.assertRaises(resume_latex.LatexCompileError):
                resume_latex.generate_pdf()
        self.assertFalse(resume_latex._compile_lock_is_held())

    def test_a_second_request_waits_for_the_in_flight_compile(self):
        # Another worker holds the lock. This request must not start a competing
        # engine process; it polls for the result instead.
        with mock.patch.object(resume_latex, 'compile_latex') as compile_mock, \
                mock.patch.object(resume_latex, '_acquire_compile_lock', return_value=None):
            with mock.patch.object(resume_latex, '_wait_for_compile', return_value=compiled(b'%PDF-1.5')) as wait:
                self.assertEqual(resume_latex.generate_pdf().pdf, b'%PDF-1.5')
        compile_mock.assert_not_called()
        wait.assert_called_once()

    def test_giving_up_on_the_wait_is_an_error_not_two_compiles(self):
        # Waiting and then compiling anyway would risk running two long
        # operations inside one request, and on the production Pi a compile that
        # outlives the Gunicorn worker timeout takes the whole site down.
        with mock.patch.object(resume_latex, 'compile_latex') as compile_mock, \
                mock.patch.object(resume_latex, '_acquire_compile_lock', return_value=None), \
                mock.patch.object(resume_latex, '_wait_for_compile', return_value=None):
            with self.assertRaises(resume_latex.LatexCompileError):
                resume_latex.generate_pdf()
        compile_mock.assert_not_called()

    @override_settings(RESUME_LATEX_MAX_COMPILES_PER_HOUR=1)
    def test_the_hourly_compile_ceiling_stops_a_compile_storm(self):
        # The per-IP limits bound one visitor; this bounds a request spread
        # across many addresses. The cache entry is already at the ceiling.
        cache.set(resume_latex._BUDGET_KEY, 1, 3600)
        with mock.patch.object(resume_latex, 'compile_latex') as compile_mock:
            with self.assertRaises(resume_latex.LatexCompileError):
                resume_latex.generate_pdf()
        compile_mock.assert_not_called()

    @override_settings(RESUME_LATEX_MAX_COMPILES_PER_HOUR=1)
    def test_the_management_command_can_warm_the_cache_past_the_ceiling(self):
        # Startup runs build_resume; an exhausted visitor budget must not leave
        # the CV cold until the hour rolls over.
        cache.set(resume_latex._BUDGET_KEY, 1, 3600)
        with mock.patch.object(resume_latex, 'compile_latex', return_value=compiled()):
            self.assertEqual(resume_latex.generate_pdf(enforce_budget=False).pages, 1)

    def test_waiting_returns_the_moment_the_result_appears(self):
        cache.set('some-key', {'pdf': b'%PDF-1.5', 'pages': 1}, 60)
        waited = resume_latex._wait_for_compile('some-key', deadline=1)
        self.assertEqual(waited.pdf, b'%PDF-1.5')

    def test_waiting_stops_when_the_holder_vanishes(self):
        self.assertIsNone(resume_latex._wait_for_compile('missing-key', deadline=1, interval=0))

    def test_waiting_gives_up_at_the_deadline(self):
        self.assertIsNone(resume_latex._wait_for_compile('missing-key', deadline=0))

    def test_a_timeout_is_reported_with_the_timeout_value(self):
        with override_settings(RESUME_LATEX_TIMEOUT=3):
            with mock.patch.object(
                resume_latex.subprocess, 'run',
                side_effect=resume_latex.subprocess.TimeoutExpired('tectonic', 3),
            ):
                with self.assertRaises(resume_latex.LatexCompileError) as caught:
                    resume_latex.compile_latex('\\documentclass{article}')
        self.assertIn('3s', str(caught.exception))

    def test_a_failed_compile_keeps_the_engine_output(self):
        import subprocess as real_subprocess

        failure = real_subprocess.CompletedProcess(
            args=['tectonic'], returncode=1, stdout='', stderr='! Undefined control sequence.',
        )
        with mock.patch.object(resume_latex.subprocess, 'run', return_value=failure):
            with self.assertRaises(resume_latex.LatexCompileError) as caught:
                resume_latex.compile_latex('\\documentclass{article}')
        self.assertIn('Undefined control sequence', caught.exception.log)

    def test_tectonic_cache_dir_is_passed_through(self):
        # The systemd sandbox makes the home directory read-only, so Tectonic's
        # package cache has to be pointable at somewhere writable.
        with override_settings(RESUME_LATEX_CACHE_DIR='/var/lib/portfolio/tectonic'):
            self.assertEqual(
                resume_latex._compile_environment()['TECTONIC_CACHE_DIR'],
                '/var/lib/portfolio/tectonic',
            )

    def test_the_engine_never_sees_the_application_secrets(self):
        # A third-party binary fed database content has no business receiving
        # SECRET_KEY, the SMTP password or a GitHub token.
        with mock.patch.dict(resume_latex.os.environ, {
            'SECRET_KEY': 'not-for-the-engine',
            'EMAIL_HOST_PASSWORD': 'not-for-the-engine',
            'GITHUB_TOKEN': 'not-for-the-engine',
        }):
            environment = resume_latex._compile_environment()
        for name in ('SECRET_KEY', 'EMAIL_HOST_PASSWORD', 'GITHUB_TOKEN'):
            self.assertNotIn(name, environment)
        self.assertIn('PATH', environment)


class DownloadFilenameTests(TestCase):
    def setUp(self):
        make_profile()

    def test_filename_comes_from_the_profile_name(self):
        self.assertEqual(resume_latex.pdf_filename(), 'armin-maddah-asl-CV.pdf')

    def test_header_offers_the_file_as_an_attachment(self):
        header = resume_latex.content_disposition()
        self.assertTrue(header.startswith('attachment;'))
        self.assertIn('filename="armin-maddah-asl-CV.pdf"', header)
        self.assertIn("filename*=UTF-8''armin-maddah-asl-CV.pdf", header)

    def test_a_nameless_profile_still_produces_a_filename(self):
        make_profile(name='')
        self.assertEqual(resume_latex.pdf_filename(), 'CV.pdf')


@override_settings(CACHES=LOCMEM)
class ResumeDownloadViewTests(TestCase):
    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        make_profile()
        self.url = reverse('resume_download')

    def test_the_pdf_is_returned_as_a_download(self):
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=compiled(b'%PDF-1.5 fake')):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(response.content, b'%PDF-1.5 fake')

    def test_a_missing_engine_is_answered_with_503_not_a_crash(self):
        with mock.patch.object(
            resume_latex, 'generate_pdf',
            side_effect=resume_latex.LatexEngineMissing('no engine'),
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, 'resume_unavailable.html')

    def test_a_failed_compile_is_answered_with_503(self):
        with mock.patch.object(
            resume_latex, 'generate_pdf',
            side_effect=resume_latex.LatexCompileError('boom', log='! error'),
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)

    def test_a_download_is_not_counted_as_a_page_view(self):
        # The dashboard counts page views; a PDF is not one, and counting it
        # would put it at the top of "top pages".
        before = PageView.objects.count()
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=compiled()):
            self.client.get(self.url)
        self.assertEqual(PageView.objects.count(), before)

    def test_the_resume_page_offers_the_download_when_an_engine_exists(self):
        with mock.patch.object(resume_latex, 'engine_available', return_value=True):
            response = self.client.get(reverse('resume'))
        self.assertContains(response, self.url)
        self.assertContains(response, 'Download CV')

    def test_the_button_is_absent_when_no_engine_is_installed(self):
        # Better no button than one that returns an error.
        with mock.patch.object(resume_latex, 'engine_available', return_value=False):
            response = self.client.get(reverse('resume'))
        self.assertNotContains(response, self.url)


@skipUnless(resume_latex.engine_available(), 'no LaTeX engine installed')
@override_settings(CACHES=LOCMEM)
class RealCompileTests(TestCase):
    """The end-to-end path, run only where an engine actually exists.

    CI has no LaTeX engine, so these skip there; everything that does not need
    an engine is covered above, by mocking the compile step.

    The LOCMEM cache override is not decoration: without it ``setUp`` calls
    ``cache.clear()`` on the *real* cache. On a server that is the same cache
    the site uses, so running the suite would wipe the warmed CV and every
    contact-form rate limit with it.
    """

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        Project.objects.all().delete()
        make_profile()

    def test_the_generated_document_compiles_to_a_pdf(self):
        compilation = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertTrue(compilation.pdf.startswith(b'%PDF'), 'output is not a PDF')
        self.assertGreater(len(compilation.pdf), 1000)

    def test_the_engine_reports_the_page_count(self):
        # The CV's one-page promise is only verifiable if the page count can be
        # read back; it comes from the engine's log.
        compilation = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertEqual(compilation.pages, 1)

    def test_unusual_content_still_compiles(self):
        # The characters that would break an unescaped document: currency,
        # percent, ampersand, braces and a non-ASCII name.
        make_profile(name='Armin & Söhne', title='R&D {Lead}')
        WorkExperience.objects.create(
            role='Engineer', company='ACME & Co', location='Düsseldorf',
            description='Cut costs by 100% using {curly} braces #1',
            start_date=date(2023, 1, 1),
        )
        Skill.objects.create(name='C++', start_date=date(2015, 1, 1))
        compilation = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertTrue(compilation.pdf.startswith(b'%PDF'))

    def test_the_download_endpoint_serves_a_real_pdf(self):
        response = self.client.get(reverse('resume_download'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_a_full_cv_is_still_one_page(self):
        """The one-page promise, against a realistically full database.

        Every section is as full as the budget allows, which is the case that
        would push the document onto a second page. If the RESUME_MAX_* caps are
        raised, this is what catches it.
        """
        Skill.objects.create(name='Python', start_date=date(2019, 1, 1), display_order=1)
        for index in range(6):
            WorkExperience.objects.create(
                role=f'Role {index}', company=f'Company {index}', location='Dortmund',
                description=(
                    'Led a team of five engineers through a platform migration. '
                    'Reduced deployment time by 60% and cut incident volume in half. '
                    'Introduced automated testing and documentation standards.'
                ),
                start_date=date(2023, 1, 1), end_date=date(2024, 1, 1),
            )
            Education.objects.create(
                degree=f'Degree {index}', institution=f'University {index}',
                location='City', start_date=date(2018, 1, 1), end_date=date(2021, 1, 1),
                description='Graduated with distinction after a thesis on applied machine learning.',
            )
            Project.objects.create(
                name=f'Project {index}', link=f'https://example.com/{index}',
                description=(
                    'A service that ingests, validates and stores large streams of '
                    'operational data. Built with a small team and deployed to a '
                    'single-board computer behind a tunnel.'
                ),
                date=date(2024, 1, 1),
            )
            Certificate.objects.create(
                name=f'Certificate {index}', institution='Issuer',
                issue_date=date(2025, 1, 1),
            )

        compilation = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertEqual(compilation.pages, 1, 'the CV no longer fits on one page')

    def test_text_is_extractable_from_the_pdf(self):
        """ATS parsers read the text layer, so it has to exist and be in order.

        Verified through the engine rather than a PDF library: the log lists the
        text the document contains, and running the same source through it is the
        cheapest way to prove the layer is real without adding a dependency.
        """
        make_profile(name='Armin Maddah Asl')
        WorkExperience.objects.create(
            role='Working Student', company='ACME', location='Dortmund',
            description='Built an internal reporting tool.', start_date=date(2023, 1, 1),
        )
        compilation = resume_latex.compile_latex(resume_latex.build_latex_source())
        # A PDF with a text layer is substantially larger than an empty image-only
        # one, and the engine reports a real page rather than zero.
        self.assertGreater(len(compilation.pdf), 5000)
        self.assertEqual(compilation.pages, 1)


class ShortenTests(TestCase):
    """Shortening is what keeps long descriptions from pushing the CV over."""

    def test_short_text_is_untouched(self):
        self.assertEqual(resume_latex.shorten('Short enough', 50), 'Short enough')

    def test_it_prefers_to_end_on_a_whole_sentence(self):
        text = 'First sentence is complete. Second sentence runs on far too long for this.'
        self.assertEqual(resume_latex.shorten(text, 40), 'First sentence is complete.')

    def test_a_tiny_first_sentence_is_ignored(self):
        # "Hi. " is only four characters; stopping there would leave a fragment
        # that says less than a truncated longer sentence would.
        text = 'Hi. Now a much longer second sentence that has to be cut somewhere.'
        shortened = resume_latex.shorten(text, 40)
        self.assertTrue(shortened.endswith('\u2026'))
        self.assertTrue(shortened.startswith('Hi. Now a much longer'))

    def test_without_a_sentence_break_it_cuts_on_a_word_and_shows_it(self):
        shortened = resume_latex.shorten('one two three four five six seven eight', 20)
        self.assertEqual(shortened, 'one two three four\u2026')
        self.assertNotIn('  ', shortened)


class ResumeOptionsTests(TestCase):
    """The tri-state selection: None means the site chooses, () means none."""

    def setUp(self):
        Skill.objects.all().delete()
        Project.objects.all().delete()
        Education.objects.all().delete()
        WorkExperience.objects.all().delete()
        Certificate.objects.all().delete()
        make_profile()
        self.experiences = [
            WorkExperience.objects.create(
                role=f'Role {index}', company=f'Company {index}',
                start_date=date(2020 + index, 1, 1),
            )
            for index in range(3)
        ]

    def test_default_options_use_the_sites_own_selection(self):
        # Ordered by -start_date, so the newest first.
        entries = resume_latex.build_resume_context()['experiences']
        self.assertEqual([e['company'] for e in entries], ['Company 2', 'Company 1', 'Company 0'])

    def test_an_empty_selection_leaves_the_section_out(self):
        context = resume_latex.build_resume_context(resume_latex.ResumeOptions(experience_ids=()))
        self.assertEqual(context['experiences'], [])

    def test_an_explicit_selection_keeps_only_those_rows(self):
        chosen = (self.experiences[0].pk,)
        context = resume_latex.build_resume_context(
            resume_latex.ResumeOptions(experience_ids=chosen)
        )
        self.assertEqual([e['company'] for e in context['experiences']], ['Company 0'])

    def test_an_explicit_selection_is_still_capped(self):
        # One page has to hold, so asking for more than the cap gives the most
        # recent ones rather than a second page.
        with override_settings(RESUME_MAX_EXPERIENCE=1):
            context = resume_latex.build_resume_context(
                resume_latex.ResumeOptions(
                    experience_ids=tuple(e.pk for e in self.experiences)
                )
            )
        self.assertEqual([e['company'] for e in context['experiences']], ['Company 2'])

    def test_the_summary_can_be_left_out(self):
        context = resume_latex.build_resume_context(
            resume_latex.ResumeOptions(include_summary=False)
        )
        self.assertEqual(context['summary'], '')
        self.assertNotIn(r'\section{Professional Summary}', resume_latex.build_latex_source(
            resume_latex.ResumeOptions(include_summary=False)
        ))

    def test_languages_can_be_left_out(self):
        context = resume_latex.build_resume_context(
            resume_latex.ResumeOptions(include_languages=False)
        )
        self.assertEqual(context['languages'], [])

    def test_keywords_follow_the_selection(self):
        python = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))
        Skill.objects.create(name='Linux', start_date=date(2018, 1, 1))
        context = resume_latex.build_resume_context(
            resume_latex.ResumeOptions(skill_ids=(python.pk,))
        )
        self.assertEqual(context['keywords'], 'Python')

    def test_project_heading_technologies_are_capped(self):
        # A dozen tags in a heading wraps to three lines and dwarfs the entry.
        # These have to be *main* skills: a project's sub-skills roll up to their
        # parent, so eight sub-skills of one parent are one heading much, not eight.
        skills = [
            Skill.objects.create(name=f'Tech {index}', start_date=date(2020, 1, 1))
            for index in range(6)
        ]
        project = Project.objects.create(name='Thing', link='https://example.com')
        project.skills_used.add(*skills)
        with override_settings(RESUME_MAX_PROJECT_TECHNOLOGIES=2):
            entry = resume_latex.build_resume_context()['projects'][0]
        self.assertEqual(entry['technologies'].count(','), 1)

    def test_the_default_project_selection_prefers_describable_projects(self):
        # A project with no description prints as a heading with nothing under it
        # — a name on a CV that reads as unfinished. Nearly every project on this
        # site has one, so the default skips the bare names.
        Project.objects.create(name='Bare', link='https://example.com/b', date=date(2025, 1, 1))
        Project.objects.create(name='Explained', link='https://example.com/e',
                               description='Does something useful.', date=date(2024, 1, 1))
        Project.objects.create(name='Also bare', link='https://example.com/ab',
                               date=date(2023, 1, 1))
        with override_settings(RESUME_MAX_PROJECTS=1):
            entries = resume_latex.build_resume_context()['projects']
        self.assertEqual([entry['name'] for entry in entries], ['Explained'])

    def test_whitespace_only_description_counts_as_no_description(self):
        Project.objects.create(name='Blank', link='https://example.com/1',
                               description='   \n  ', date=date(2025, 1, 1))
        Project.objects.create(name='Real', link='https://example.com/2',
                               description='Actual text.', date=date(2024, 1, 1))
        with override_settings(RESUME_MAX_PROJECTS=1):
            entries = resume_latex.build_resume_context()['projects']
        self.assertEqual([entry['name'] for entry in entries], ['Real'])

    def test_an_explicit_project_selection_is_not_reordered(self):
        # The preference is for the default only: if the owner deliberately picks
        # a project with no description, they get it.
        bare = Project.objects.create(name='Bare', link='https://example.com/b',
                                      date=date(2025, 1, 1))
        Project.objects.create(name='Explained', link='https://example.com/e',
                               description='Does something useful.', date=date(2024, 1, 1))
        context = resume_latex.build_resume_context(
            resume_latex.ResumeOptions(project_ids=(bare.pk,))
        )
        self.assertEqual([entry['name'] for entry in context['projects']], ['Bare'])

    def test_the_form_preselects_exactly_the_default_cv(self):
        # The generator pre-ticks `default_selection()`, so it must agree with the
        # document the plain download button produces. This is the invariant that
        # broke when the project preference was added in only one of the two
        # places, which is why it is asserted rather than assumed.
        Project.objects.create(name='Bare', link='https://example.com/b', date=date(2026, 1, 1))
        Project.objects.create(name='Explained', link='https://example.com/e',
                               description='Does something useful.', date=date(2025, 1, 1))
        defaults = resume_latex.default_selection()
        shown = [
            entry['name'] for entry in resume_latex.build_resume_context()['projects']
        ]
        preselected = [
            Project.objects.get(pk=pk).name for pk in defaults['projects']
        ]
        self.assertEqual(preselected, shown)

    def test_the_default_selection_matches_what_the_default_cv_shows(self):
        # The generator pre-ticks these, so they must agree with the document the
        # plain download button produces — otherwise the page would open showing
        # a selection that is not the CV it claims to be.
        defaults = resume_latex.default_selection()
        context = resume_latex.build_resume_context()
        self.assertEqual(len(defaults['experiences']), len(context['experiences']))
        # The newest row is included, and the oldest of the three falls outside
        # the cap only if the cap is smaller than three.
        self.assertIn(self.experiences[-1].pk, defaults['experiences'])
        self.assertEqual(
            len(defaults['experiences']),
            min(3, settings.RESUME_MAX_EXPERIENCE),
        )
        # The on/off sections start on, matching what the default CV shows.
        self.assertTrue(defaults['include_summary'])
        self.assertTrue(defaults['include_languages'])

    def test_budget_caps_are_reported_for_every_section(self):
        caps = resume_latex.budget_caps()
        self.assertEqual(set(caps), set(resume_latex.SELECTABLE))
        self.assertTrue(all(isinstance(value, int) and value > 0 for value in caps.values()))


@override_settings(CACHES=LOCMEM)
class ResumeGeneratorViewTests(TestCase):
    """The customization page and the download it produces."""

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        Project.objects.all().delete()
        Education.objects.all().delete()
        WorkExperience.objects.all().delete()
        Certificate.objects.all().delete()
        make_profile()
        self.url = reverse('resume_generator')
        self.experience = WorkExperience.objects.create(
            role='Engineer', company='ACME', start_date=date(2023, 1, 1),
        )
        self.published = Project.objects.create(
            name='Published', link='https://example.com/1', date=date(2024, 1, 1),
        )
        self.unpublished = Project.objects.create(
            name='Unpublished', link='https://example.com/2',
            date=date(2024, 1, 1), is_published=False,
        )
        self.skill = Skill.objects.create(name='Python', start_date=date(2019, 1, 1))

    def post_data(self, **overrides):
        data = {
            'include_summary': 'on',
            'include_languages': 'on',
            'experiences': [str(self.experience.pk)],
            'educations': [],
            'projects': [str(self.published.pk)],
            'skills': [str(self.skill.pk)],
            'certificates': [],
        }
        data.update(overrides)
        return data

    def test_the_page_lists_every_section(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for label in ('Experience', 'Education', 'Selected projects', 'Skill categories',
                      'Certifications'):
            self.assertContains(response, label)
        self.assertContains(response, 'Generate PDF')

    def test_the_page_opens_on_the_default_selection(self):
        # Opening on an empty form would make the visitor rebuild the
        # recommendation by hand.
        response = self.client.get(self.url)
        self.assertContains(response, 'checked')
        self.assertContains(response, f'value="{self.published.pk}"')
        # The on/off sections must start on as well, not just the id lists. The
        # id attribute sits between name and checked, so match the whole tag.
        html = response.content.decode()
        for field in ('include_summary', 'include_languages'):
            self.assertRegex(html, rf'name="{field}"[^>]*\bchecked\b')

    def test_the_caps_are_stated_on_the_page(self):
        response = self.client.get(self.url)
        for cap in resume_latex.budget_caps().values():
            self.assertContains(response, str(cap))

    def test_submitting_returns_a_pdf_for_the_selection(self):
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=compiled(b'%PDF-ok')) as generate:
            response = self.client.post(self.url, self.post_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(response.content, b'%PDF-ok')

        options = generate.call_args.args[0]
        self.assertIsInstance(options, resume_latex.ResumeOptions)
        self.assertEqual(options.experience_ids, (self.experience.pk,))
        self.assertEqual(options.project_ids, (self.published.pk,))

    def test_an_empty_selection_is_honoured_rather_than_treated_as_all(self):
        # Unticking everything must produce a nearly empty CV, not every row.
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=compiled()) as generate:
            self.client.post(self.url, self.post_data(
                experiences=[], projects=[], skills=[], certificates=[],
                include_summary='', include_languages='',
            ))
        options = generate.call_args.args[0]
        self.assertEqual(options.experience_ids, ())
        self.assertEqual(options.project_ids, ())
        self.assertFalse(options.include_summary)
        self.assertFalse(options.include_languages)

    def test_an_unpublished_project_cannot_be_put_into_a_cv(self):
        # The id is real, but the project is hidden. Validation is what stops a
        # visitor reading content the owner has taken off the site.
        response = self.client.post(self.url, self.post_data(
            projects=[str(self.unpublished.pk)],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])
        self.assertContains(response, 'Select a valid choice')

    def test_an_id_that_does_not_exist_is_rejected(self):
        response = self.client.post(self.url, self.post_data(skills=['999999']))
        self.assertIn('text/html', response['Content-Type'])
        self.assertContains(response, 'Select a valid choice')

    def test_an_id_belonging_to_another_model_is_rejected(self):
        # A project id is a valid integer but not a skill; passing one through
        # the wrong field must not reach a query.
        response = self.client.post(self.url, self.post_data(skills=[str(self.published.pk)]))
        self.assertIn('text/html', response['Content-Type'])

    def test_a_null_byte_in_a_selection_is_rejected(self):
        response = self.client.post(self.url, self.post_data(
            experiences=['1\x00'], projects=[''], skills=['1 OR 1=1'],
        ))
        self.assertIn('text/html', response['Content-Type'])

    def test_a_compile_failure_is_answered_with_503(self):
        with mock.patch.object(
            resume_latex, 'generate_pdf',
            side_effect=resume_latex.LatexCompileError('boom'),
        ):
            response = self.client.post(self.url, self.post_data())
        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, 'resume_unavailable.html')

    def test_the_generator_is_linked_from_the_resume_page(self):
        with mock.patch.object(resume_latex, 'engine_available', return_value=True):
            response = self.client.get(reverse('resume'))
        self.assertContains(response, self.url)

    def test_the_generator_is_not_a_broken_link_without_an_engine(self):
        # The page still opens and explains itself, rather than 500-ing; only the
        # submit would fail, and the intro says where the PDF comes from.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


@override_settings(CACHES=LOCMEM)
class ResumeSecurityTests(TestCase):
    """What a visitor cannot do through the generator."""

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        make_profile()

    def test_no_free_text_reaches_the_document(self):
        # The form has no text input at all: every field is a checkbox list of
        # ids. Posting text into those fields must be rejected, so there is no
        # path by which a visitor's own LaTeX could be compiled.
        response = self.client.post(reverse('resume_generator'), {
            'skills': [r'\input{/etc/passwd}'],
        })
        self.assertIn('text/html', response['Content-Type'])
        source = resume_latex.build_latex_source()
        # The preamble legitimately uses \input{glyphtounicode}, so assert on the
        # injected text itself rather than on the command.
        self.assertNotIn('/etc/passwd', source)
        self.assertNotIn(r'\input{/etc/passwd}', source)

    def test_the_options_type_cannot_carry_arbitrary_latex(self):
        # Even if a caller built ResumeOptions by hand, ids are the only input,
        # and they only ever select rows that already exist.
        options = resume_latex.ResumeOptions(project_ids=(1, 2))
        source = resume_latex.build_latex_source(options)
        self.assertIn(r'\end{document}', source)
        self.assertEqual(source.count(r'\end{document}'), 1)
