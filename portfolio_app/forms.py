from django import forms
from django.conf import settings
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV3

from . import resume_latex


class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(max_length=2000, widget=forms.Textarea)
    # Honeypot field — hidden from humans, bots fill it in
    url = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'autocomplete': 'off',
        'tabindex': '-1',
        'style': 'position:absolute;left:-9999px;',
    }))

    if not settings.DEBUG:
        # Skipped in development: without real keys the browser cannot produce a
        # reCAPTCHA v3 token, which would block every local submission. Production
        # always requires the captcha (see settings.RECAPTCHA_*).
        captcha = ReCaptchaField(widget=ReCaptchaV3)


class ResumeOptionsForm(forms.Form):
    """Which of the site's content the generated CV contains.

    Every selection is a ``ModelMultipleChoiceField`` bound to the querysets the
    CV builder itself uses (``resume_latex.SELECTABLE``). That is what makes the
    form safe rather than the view: Django checks each submitted id against the
    queryset, so an id for an unpublished project, or for a completely different
    model, is a validation error instead of something that reaches a query. No
    id, and no free text of any kind, is ever interpolated into the LaTeX.

    An unsubmitted or cleared field simply means "none of these", which is why
    the template sends the whole set rather than treating absence as "all".
    """

    include_summary = forms.BooleanField(required=False, label='Professional summary')
    include_languages = forms.BooleanField(required=False, label='Languages')

    # The selectable sections, in the order the CV prints them, with the heading
    # the generator page shows. The view pairs each with its bound field and its
    # cap; a Django template cannot look a field up by a variable name, so the
    # pairing has to happen in Python.
    SECTION_LABELS = (
        ('experiences', 'Experience'),
        ('educations', 'Education'),
        ('projects', 'Selected projects'),
        ('skills', 'Skill categories'),
        ('certificates', 'Certifications'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field, (queryset, _cap) in resume_latex.SELECTABLE.items():
            self.fields[field] = forms.ModelMultipleChoiceField(
                queryset=queryset(),
                required=False,
                label=field.capitalize(),
                widget=forms.CheckboxSelectMultiple(
                    attrs={'class': 'mt-1 h-4 w-4 shrink-0 accent-emphasis'},
                ),
            )

    def sections(self):
        """The selectable sections as (name, label, bound field) triples."""
        return [
            (name, label, self[name]) for name, label in self.SECTION_LABELS
        ]

    def to_options(self):
        """The validated selections, as the builder's ``ResumeOptions``.

        Only called on a valid form, so each value is a queryset of real rows.
        ``tuple()`` rather than ``None`` is what tells the builder this was an
        explicit choice — including "none" — instead of a request for the
        default.
        """
        selected = {
            field: tuple(row.pk for row in self.cleaned_data[field])
            for field, _cap in resume_latex.SELECTABLE.items()
        }
        return resume_latex.ResumeOptions(
            experience_ids=selected['experiences'],
            education_ids=selected['educations'],
            project_ids=selected['projects'],
            skill_ids=selected['skills'],
            certificate_ids=selected['certificates'],
            include_summary=self.cleaned_data['include_summary'],
            include_languages=self.cleaned_data['include_languages'],
        )