You are extracting from a **Ruby on Rails** codebase. Apply these
conventions when picking entities, edges, and call chains for the brain
store.

## Routing & entry points

- `config/routes.rb` is the authoritative URL table.
  - `resources :foos` → standard REST routes mapped to `FoosController`.
  - `resource :foo` (singular) → singular REST routes.
  - `get "foo" => "foos#bar"` → explicit route to `FoosController#bar`.
  - `namespace :api do ... end`, `scope :v1 do ... end` add prefixes.
  - `concern :soft_deletable do ... end` + `concerns :soft_deletable` —
    record the concern, then its included routes.
- Controllers live in `app/controllers/**/*_controller.rb`. The action
  methods (`index`, `show`, `create`, `update`, `destroy`, plus any
  custom action listed in routes) are entry points.
- `before_action :authenticate_user!`, `skip_before_action`,
  `around_action` — record as ANNOTATES on the affected actions.
- `rescue_from ExceptionClass, with: :handler` — record as an
  exception-mapping edge.

## ActiveRecord (the data layer)

- `class Foo < ApplicationRecord` (or `< ActiveRecord::Base` in older
  apps) is a model. Methods worth extracting in full:
  - **Scopes**: `scope :active, -> { where(active: true) }` — these are
    chainable predicates the rest of the app reuses.
  - **Class methods**: `def self.find_by_lob(lob)` — domain queries.
  - **Instance methods** with real bodies (e.g. `def total_price`).
- Skip generated columns (`belongs_to`, `has_many`, `has_one`,
  `has_and_belongs_to_many`) as bodies, but capture them as metadata —
  they tell you the relationship graph.
- Validation DSL (`validates :foo, presence: true`) — record on the
  model as metadata, do not extract as a method body.
- Callbacks (`before_save`, `after_create`, `after_commit`) — capture
  the callback target method as a CALLS edge from the lifecycle event.

## Queries & SQL

- `Foo.where(...)`, `Foo.joins(...)`, `Foo.includes(...)`,
  `Foo.eager_load(...)` chains — walk the chain to assemble the
  predicate. The terminator (`first`, `last`, `find`, `find_by`, `to_a`,
  `pluck`, iteration) materialises the query.
- `Foo.find_by_sql("SELECT ...")` and
  `ActiveRecord::Base.connection.execute("...")` are raw SQL — capture
  verbatim as `query_text`.
- Arel: `Foo.arel_table[:column].eq(value)` — capture the column and
  predicate; usually appears inside a scope.

## Concerns & mixins

- `module Foo::Concerns::Bar` `extend ActiveSupport::Concern` — concerns
  are mixed into models / controllers via `include Bar`. The concern's
  methods become real methods on the host class. Treat the concern's
  `included { ... }` block as configuration on the host.

## Background jobs & mailers

- `class FooJob < ApplicationJob` with `def perform(...)` — entry point,
  triggered by `FooJob.perform_later(args)`. Capture both the trigger
  call and the perform body.
- `class FooMailer < ApplicationMailer` with `def welcome(user)` —
  similar shape; trigger is `FooMailer.welcome(user).deliver_later`.

## Views & helpers — usually skip

- ERB / HAML templates are not code entities. Capture only when the user
  asks about a specific partial flow.
- `app/helpers/**/*_helper.rb` modules — extract only when explicitly
  referenced by a controller or view in scope.

## Common false positives

- `attr_accessor`, `attr_reader`, `attr_writer` generate methods; do not
  emit edges to the symbols themselves.
- `delegate :foo, to: :bar` synthesises `def foo; bar.foo; end`; record
  the delegation but not "delegate" as a callee.
- `Rails.logger.info(...)`, `Rails.cache.fetch(...)` — observability /
  caching infrastructure; ignore unless cache keys are the question.
- `params[:foo]`, `session[:bar]`, `cookies[:baz]` — request bag
  accesses; ignore as edges.

## Service objects (a common Rails pattern)

- `app/services/**/*.rb` — `class FooService` with `def call`. These are
  business logic; extract in full. Common variants: `Interactor`,
  `Command`, `UseCase`. Treat the `call` / `perform` / `execute` method
  as the entry point.

## When in doubt

- Controller → service / model is the canonical chain. Many Rails apps
  put domain logic on models (fat models / skinny controllers); some
  use service objects. Both are valid — follow whatever the project
  does.
- Prefer one batched `extract_methods_from_class` per file over many
  per-method calls.
