You are extracting from a **NestJS** codebase. Apply these conventions
when picking entities, edges, and call chains for the brain store.

## Module / DI graph

- `@Module({ imports, controllers, providers, exports })` declares a NestJS
  module. The module file is rarely worth extracting line-by-line, but its
  `providers` array is the source of truth for DI bindings — when a class
  appears in `providers`, every other class that injects it gets a
  DEPENDS_ON edge.
- `@Global()` modules expose every provider app-wide; treat their providers
  as available to any class.
- Dynamic modules (`forRoot`, `forFeature`, `forRootAsync`) wire
  configuration; their static method bodies are config, not business logic.

## Controllers (HTTP entry points)

- `@Controller('path')` marks an entry class. Combined with method
  decorators (`@Get('/x')`, `@Post('/x')`, `@Put`, `@Patch`, `@Delete`,
  `@All`) to assemble routes.
- Parameter decorators describe inputs: `@Body()`, `@Param('id')`,
  `@Query('q')`, `@Headers()`, `@Req()`, `@Res()`. Capture the typed
  parameter; do not record a CALLS edge to the decorator.
- `@UseGuards(...)`, `@UseInterceptors(...)`, `@UsePipes(...)`,
  `@UseFilters(...)` — record each guard/interceptor/pipe class as a
  DEPENDS_ON edge from the handler. Their behaviour shapes the request.

## Services & providers

- `@Injectable()` is the canonical provider marker. Constructor parameters
  with types are DI dependencies — every typed param is a DEPENDS_ON edge.
- `@Inject(TOKEN)` overrides type-based injection with an explicit token;
  the token usually maps to a `useFactory` / `useValue` provider in the
  module.
- `@Optional()` parameters are still DEPENDS_ON, just nullable.

## Database access

- **TypeORM**: `@InjectRepository(Foo)` injects a `Repository<Foo>`. Calls
  like `repo.find(...)`, `repo.findOne({ where })`, `repo.save(...)` are
  data-access. The query builder
  (`repo.createQueryBuilder('f').where(...).getMany()`) is also worth
  capturing as `query_text`.
- **TypeORM entities**: `@Entity()` classes — annotation-only DTOs;
  capture as type targets but do not extract methods unless they have
  a real method body (e.g. instance helpers).
- **Prisma**: `prisma.foo.findUnique(...)`, `prisma.foo.create(...)`. The
  property name (`foo`) is the model. Capture it as the data-access
  target.
- **Mongoose**: `@InjectModel(Foo.name)` injects a `Model<FooDocument>`;
  `model.find(...)` etc. follow the same pattern.

## Cross-cutting

- `@nestjs/cqrs`: `commandBus.execute(new FooCommand(...))` and
  `queryBus.execute(new FooQuery(...))` are CALLS edges to the
  corresponding `*Handler` (look for `@CommandHandler(FooCommand)` /
  `@QueryHandler(FooQuery)`).
- `@nestjs/microservices`: `@MessagePattern('topic')` and
  `@EventPattern('topic')` are non-HTTP entry points; treat them like
  controllers but tag the trigger type as "message" / "event".
- `@nestjs/schedule`: `@Cron('* * * * *')` and `@Interval(ms)` are
  scheduled triggers — capture the schedule expression as metadata.

## DTOs to skip

- `*Dto` classes that are pure shape declarations with `class-validator`
  decorators only (`@IsString()`, `@IsEmail()`, `@IsOptional()`, etc.).
- `*Entity` TypeORM classes whose methods are only column getters.

## Common false positives

- `class-transformer` decorators (`@Expose`, `@Transform`, `@Type`) are
  serialisation hints, not method calls.
- `RxJS` operators inside service methods (`pipe`, `map`, `mergeMap`) are
  flow control — extract the inner callable, not `pipe`.
- `Logger` (built into Nest) calls — not edges worth recording.

## When in doubt

- Controller → service → repository is the canonical chain. The module
  file tells you which concrete class is bound to each token.
- Prefer one batched `extract_methods_from_class` per file over many
  per-method calls.
