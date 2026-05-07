/**
 * Internal types for the JPA/Hibernate @Entity extractor.
 */

export interface JpaField {
  javaName: string;            // Java field name (camelCase)
  dbColumnName: string;        // from @Column(name=...) or snake_case conversion
  javaType: string;            // String, Long, Integer, Boolean, LocalDateTime, etc.
  dbType: string;              // inferred SQL type
  nullable: boolean;           // @Column(nullable=false) → false, default true
  isPrimaryKey: boolean;       // @Id present
  isGeneratedValue: boolean;
  isForeignKey: boolean;       // @ManyToOne, @OneToOne present
  referencedEntity: string | null; // from @ManyToOne field type
  referencedColumn: string | null; // from @JoinColumn(referencedColumnName=...)
  joinColumnName: string | null;   // from @JoinColumn(name=...)
  columnLength: number | null;     // from @Column(length=...)
  unique: boolean;             // @Column(unique=true)
  defaultValue: string | null; // from @Column(columnDefinition=...) or @Value
}

export interface JpaEntity {
  className: string;           // Java class name
  tableName: string;           // from @Table(name=...) or snake_case(className)
  schemaName: string;          // from @Table(schema=...)
  fields: JpaField[];
  sourceFile: string;          // relative path
}
