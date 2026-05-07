/**
 * Internal types for the SQLAlchemy / Django ORM extractor.
 */

export interface PythonOrmField {
  name: string;                     // Python attribute name
  dbColumnName: string;             // from Column(..., name="...") or snake_case
  dbType: string;                   // String, Integer, BigInteger, Boolean, DateTime, etc.
  nullable: boolean;
  isPrimaryKey: boolean;
  isForeignKey: boolean;
  foreignKeyTarget: string | null;  // "other_table.id"
  serverDefault: string | null;
  unique: boolean;
}

export interface PythonOrmModel {
  className: string;
  tableName: string;        // from __tablename__ / Meta.db_table or snake_case(className)
  fields: PythonOrmField[];
  orm: "sqlalchemy" | "django";
  sourceFile: string;
}
