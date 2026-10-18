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


@override_settings(CACHES=LOCMEM)
class CompilePipelineTests(TestCase):
    """Compiling and caching, with the engine itself mocked out."""

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        make_profile()

    def test_a_missing_engine_is_reported_as_such(self):
        with mock.patch.object(resume_latex, 'find_engine', return_value=None):
            with self.assertRaises(resume_latex.LatexEngineMissing):
                resume_latex.generate_pdf()

    def test_the_pdf_is_compiled_once_and_then_cached(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=b'%PDF-1.5') as compile_mock:
            first = resume_latex.generate_pdf()
            second = resume_latex.generate_pdf()
        self.assertEqual(first, second)
        self.assertEqual(compile_mock.call_count, 1)

    def test_force_recompiles_and_refreshes_the_cache(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=b'old') as compile_mock:
            resume_latex.generate_pdf()
            with mock.patch.object(resume_latex, 'compile_latex', return_value=b'new') as second:
                self.assertEqual(resume_latex.generate_pdf(force=True), b'new')
        self.assertEqual(compile_mock.call_count, 1)
        self.assertEqual(second.call_count, 1)

    def test_the_cache_key_follows_the_content(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=b'%PDF-1.5') as compile_mock:
            resume_latex.generate_pdf()
            # Changing the site changes the LaTeX, which must produce a different
            # key — otherwise an edited CV would serve the previous PDF.
            make_profile(name='Someone Else')
            resume_latex.generate_pdf()
        self.assertEqual(compile_mock.call_count, 2)

    def test_caching_can_be_turned_off(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=b'%PDF-1.5') as compile_mock:
            resume_latex.generate_pdf(use_cache=False)
            resume_latex.generate_pdf(use_cache=False)
        self.assertEqual(compile_mock.call_count, 2)

    def test_the_compile_lock_is_released_afterwards(self):
        with mock.patch.object(resume_latex, 'compile_latex', return_value=b'%PDF-1.5'):
            resume_latex.generate_pdf()
        self.assertIsNone(cache.get(resume_latex._lock_key()))

    def test_the_lock_is_released_even_when_the_compile_fails(self):
        error = resume_latex.LatexCompileError('boom')
        with mock.patch.object(resume_latex, 'compile_latex', side_effect=error):
            with self.assertRaises(resume_latex.LatexCompileError):
                resume_latex.generate_pdf()
        self.assertIsNone(cache.get(resume_latex._lock_key()))

    def test_a_second_request_waits_for_the_in_flight_compile(self):
        # Another worker holds the lock. This request must not start a competing
        # engine process; it polls for the result instead.
        with mock.patch.object(resume_latex, 'compile_latex') as compile_mock, \
                mock.patch.object(resume_latex.cache, 'add', return_value=False):
            with mock.patch.object(resume_latex, '_wait_for_compile', return_value=b'%PDF-1.5') as wait:
                self.assertEqual(resume_latex.generate_pdf(), b'%PDF-1.5')
        compile_mock.assert_not_called()
        wait.assert_called_once()

    def test_giving_up_on_the_wait_is_an_error_not_two_compiles(self):
        # Waiting and then compiling anyway would risk running two long
        # operations inside one request, and on the production Pi a compile that
        # outlives the Gunicorn worker timeout takes the whole site down.
        with mock.patch.object(resume_latex, 'compile_latex') as compile_mock, \
                mock.patch.object(resume_latex.cache, 'add', return_value=False), \
                mock.patch.object(resume_latex, '_wait_for_compile', return_value=None):
            with self.assertRaises(resume_latex.LatexCompileError):
                resume_latex.generate_pdf()
        compile_mock.assert_not_called()

    def test_waiting_returns_the_moment_the_result_appears(self):
        cache.set('some-key', b'%PDF-1.5', 60)
        self.assertEqual(resume_latex._wait_for_compile('some-key', deadline=1), b'%PDF-1.5')

    def test_waiting_stops_when_the_holder_vanishes(self):
        self.assertIsNone(resume_latex._wait_for_compile('missing-key', deadline=1, interval=0))

    def test_waiting_gives_up_at_the_deadline(self):
        cache.set(resume_latex._lock_key(), 'compiling', 60)
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
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=b'%PDF-1.5 fake'):
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
        with mock.patch.object(resume_latex, 'generate_pdf', return_value=b'%PDF-1.5'):
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
class RealCompileTests(TestCase):
    """The end-to-end path, run only where an engine actually exists.

    CI has no LaTeX engine, so these skip there; everything that does not need
    an engine is covered above, by mocking the compile step.
    """

    def setUp(self):
        cache.clear()
        Skill.objects.all().delete()
        Project.objects.all().delete()
        make_profile()

    def test_the_generated_document_compiles_to_a_pdf(self):
        pdf = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertTrue(pdf.startswith(b'%PDF'), 'output is not a PDF')
        self.assertGreater(len(pdf), 1000)

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
        pdf = resume_latex.compile_latex(resume_latex.build_latex_source())
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_the_download_endpoint_serves_a_real_pdf(self):
        response = self.client.get(reverse('resume_download'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
