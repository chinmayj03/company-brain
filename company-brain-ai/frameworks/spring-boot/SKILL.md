You are extracting from a **Spring Boot** codebase. Apply these conventions
when picking entities, edges, and call chains for the brain store.

## Routing & entry-point annotations

- `@RestController`, `@Controller` mark a class as an HTTP entry point.
- `@RequestMapping("/path")` at the class sets the base path; combine with
  `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`,
  `@PatchMapping` on methods to assemble the full route.
- `@RequestBody`, `@PathVariable`, `@RequestParam`, `@RequestHeader` mark
  inputs — capture the type but do not extract the parameter wrapper itself.
- `@ResponseStatus`, `@ExceptionHandler`, `@ControllerAdvice` are response /
  error-mapping concerns; record only when the user asks about error paths.

## Service-layer annotations

- `@Service`, `@Component`, `@Configuration` mark a Spring bean. Treat the
  bean as a function_node target for CALLS edges from the controller.
- `@Repository` may sit on (a) a Spring Data JPA interface — methods are
  implicit queries — or (b) a hand-written class. In both cases the methods
  count as data-access entry points.
- `@Autowired`, `@Inject`, and constructor injection (often via Lombok's
  `@RequiredArgsConstructor`) all create DEPENDS_ON edges. Lombok's
  `@RequiredArgsConstructor` synthesises a constructor that takes every
  `private final` field — the constructor itself is not user-written code.
- `@Transactional` is a boundary — record it as ANNOTATES on the method, not
  as a call.
- `@Async`, `@Scheduled`, `@EventListener` change the call shape; record the
  trigger as metadata on the method.

## SQL extraction

- JPA repository interfaces: methods named `findBy<Field>`, `existsBy<Field>`,
  etc. compose queries by name. `@Query("SELECT ...")` annotations carry the
  raw JPQL/SQL — extract that as `query_text` on the method.
- jOOQ DSL chains: `dslContext.select(...).from(TABLE).where(...).fetch()`.
  Walk the chain to assemble the table list. The argument to `.from(...)`
  is usually a generated `Tables.MY_TABLE` constant — keep the bare table
  name, not the constant alias.
- Column setters often look like `r.value1()`, `r.value2()`, `r.lobName(...)`.
  When you see `lobName(x)`, the column is "lob" or "lob_name"; check the
  surrounding `Field<>` declarations to disambiguate.

## DTOs & types to skip (do NOT call extract_methods_from_class on these)

- `*Request`, `*Response`, `*DTO`, `*Dto` classes whose methods are only
  getters/setters/equals/hashCode/toString.
- `*Entity` JPA classes — annotation-only, no business logic.
- `*Config`, `*Configuration` Spring beans where every method is `@Bean`
  factory wiring rather than business logic.
- `*Properties` `@ConfigurationProperties` value classes.
- Any record / Lombok `@Value` / `@Data` class with no method body of substance.

These bloat the brain with shapes the agent already infers from types.

## Common false positives to avoid

- `@RequiredArgsConstructor`, `@AllArgsConstructor`, `@NoArgsConstructor`
  (Lombok) generate constructors. Do not emit a CALLS edge to the
  annotation itself.
- `@Slf4j` (Lombok) generates a `log` field; `log.info(...)` calls are
  observability noise, not edges.
- `@Builder` generates a builder class; the synthetic `MyClass.builder()`
  call is not a real method on `MyClass`.
- `Optional.ofNullable(...)`, `List.of(...)`, `Stream.of(...)` are JDK
  factory methods; ignore unless the user asks about JDK usage.
- `ResponseEntity.ok(...)`, `ResponseEntity.status(...)` are response
  wrappers — record only the wrapped payload type.

## When in doubt

- The handler method is the spine. Walk callees once; do not chase deeply
  into framework internals (Spring Security filter chain, Jackson
  serialization, AOP advisors).
- Prefer one batched `extract_methods_from_class` per file over many
  per-method calls — the ContextAgent extractor handles batching.
