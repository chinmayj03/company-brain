You are extracting from a **Django** codebase. Apply these conventions
when picking entities, edges, and call chains for the brain store.

## URL routing & entry points

- `urls.py` files map paths to views: `path("foo/", views.FooView.as_view())`,
  `path("bar/<int:pk>/", views.bar)`, `re_path(...)`, `include("app.urls")`.
- Walk the include chain to assemble the full path. `include("app.urls")`
  means the app's `urls.py` is mounted at the parent's prefix.
- Views can be (a) function-based: `def my_view(request, pk):` decorated
  with `@require_http_methods(["GET", "POST"])` or `@api_view(["GET"])` for
  DRF, or (b) class-based: `class FooView(View)` / `ListView` / `APIView`.
- For class-based views, the entry methods are `get`, `post`, `put`,
  `patch`, `delete`, `dispatch`. Extract those, not `as_view()`.
- `@login_required`, `@permission_required`, `@csrf_exempt` are guards —
  record as ANNOTATES, not CALLS.

## DRF (Django REST Framework) specifics

- `class FooViewSet(viewsets.ModelViewSet)` exposes CRUD methods
  (`list`, `retrieve`, `create`, `update`, `partial_update`, `destroy`)
  plus any `@action(detail=True, methods=["post"])` extras.
- Routers (`DefaultRouter().register('foo', FooViewSet)`) generate the URL
  table at runtime — assume conventional REST paths if the registration
  is in `urls.py`.
- `serializer_class = FooSerializer` is a DEPENDS_ON edge; the serializer
  itself is usually a DTO.

## ORM & QuerySet patterns

- `Model.objects.filter(...)`, `.get(...)`, `.exclude(...)`, `.annotate(...)`
  build a QuerySet. The chain ends when you hit `.first()`, `.last()`,
  `.all()`, `.count()`, `.exists()`, `.values()`, `.values_list()`, or an
  iteration. Capture the model and the predicate kwargs as `query_text`.
- `Model.objects.raw("SELECT ...")` and `cursor.execute(...)` (via
  `connection.cursor()`) are raw SQL — capture verbatim.
- Custom managers: `class FooManager(models.Manager)` with custom
  `get_queryset` or domain methods (`def active(self):`). Extract these
  in full — they are business logic in disguise.
- Custom QuerySet: `class FooQuerySet(models.QuerySet)` chained methods.
  Extract in full.

## Models to capture, but lightly

- `class Foo(models.Model)` — capture the class as a node and its column
  fields as metadata. Skip Django-generated dunder methods.
- Real method bodies on the model (`def is_active(self):`,
  `def calculate_total(self):`) ARE worth extracting — that's where
  business logic hides.
- `class Meta:` inner classes are configuration; do not extract them.

## Forms & serializers — usually skip

- `class FooForm(forms.ModelForm)` and `class FooSerializer(ModelSerializer)`
  are shape declarations. Capture their target models but do not extract
  every `validate_<field>` method unless the user is asking about
  validation paths specifically.

## Common false positives

- Django signals (`post_save.connect(...)`, `@receiver(post_save, ...)`) —
  capture the connection as an edge from the trigger model to the handler;
  do not chase the signal framework itself.
- `gettext_lazy("...")`, `_("...")` — i18n wrappers; ignore.
- `reverse("url-name")` is a name lookup, not a call to the view function
  directly. Capture it only if the user asked about cross-link chains.
- `super().get_queryset()`, `super().form_valid()` — record the method
  override but not "super" as a target; the supertype is the parent class.

## Settings & middleware

- `settings.INSTALLED_APPS`, `settings.MIDDLEWARE`, `settings.DATABASES`
  define the app boundary; reference them when answering "what runs
  before my view". Do not extract them as code entities.

## When in doubt

- View → service / model method is the canonical chain. Most Django
  projects skip an explicit service layer; the model managers fill that
  role. Extract managers and querysets in full when the user asks about
  domain logic.
- Prefer one batched `extract_methods_from_class` per file over many
  per-method calls.
