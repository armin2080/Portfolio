from django import forms
from django.conf import settings
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV3


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