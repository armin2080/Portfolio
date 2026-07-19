from django import forms
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
    captcha = ReCaptchaField(widget=ReCaptchaV3)