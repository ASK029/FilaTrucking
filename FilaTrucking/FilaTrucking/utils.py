class TailwindFormMixin:
    """
    Apply standard Tailwind CSS styling to all Django form fields.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            # Add base classes for text/password/email inputs, textareas, selects
            base_class = "w-full bg-slate-800 border border-slate-600 rounded-xl px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-accent focus:border-transparent text-sm"
            
            # Check if class attr already exists string or dictionary to prepend
            attrs = field.widget.attrs
            if 'class' in attrs:
                attrs['class'] += f" {base_class}"
            else:
                attrs['class'] = base_class
