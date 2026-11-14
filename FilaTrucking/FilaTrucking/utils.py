class TailwindFormMixin:
    """
    Apply standard Tailwind CSS styling to all Django form fields.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            widget = field.widget
            widget_type = widget.__class__.__name__
            
            # Add base classes for text/password/email inputs, textareas, selects, and date/time inputs
            base_class = "w-full bg-slate-800 border border-slate-600 rounded-xl px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-accent focus:border-transparent text-sm accent-accent"
            
            # Check if class attr already exists string or dictionary to prepend
            attrs = field.widget.attrs
            # every control gets an accessible name even where a template renders it without its <label> (formset rows,
            # inline filters): axe reported 47 unlabeled form controls (QA-033)
            attrs.setdefault('aria-label', str(field.label or field_name.replace('_', ' ').capitalize()))
            if 'class' in attrs:
                attrs['class'] += f" {base_class}"
            else:
                attrs['class'] = base_class
