const { DataTypes, Model } = require("sequelize");
const sequelize = require("../config/database");

/**
 * Una matricula asocia a un estudiante con una asignatura, identificada
 * por su codigo (ej. "PY-101") - un dato propio de Matriculas, no una
 * referencia a ningun otro sistema. Matriculas no valida ni conoce el
 * significado de ese codigo (no sabe si existe, ni si tiene cupos): solo
 * lo registra, tal como lo describe el enunciado para este momento del
 * proyecto.
 *
 * Cuando exista la integracion con Cupos, la resolucion de este codigo
 * hacia un curso real (y su curso_id en Cupos) vivira en la capa de
 * integracion, no aqui - ver README, seccion "Estado actual de la
 * integracion".
 */
class Enrollment extends Model {}

Enrollment.init(
  {
    id: {
      type: DataTypes.UUID,
      defaultValue: DataTypes.UUIDV4,
      primaryKey: true,
    },
    student_id: {
      type: DataTypes.UUID,
      allowNull: false,
    },
    codigo_curso: {
      type: DataTypes.STRING(30),
      allowNull: false,
      comment: "Codigo de la asignatura tal como lo escribe quien matricula (sin validar)",
    },
    estado: {
      type: DataTypes.ENUM("PENDIENTE", "CONFIRMADA", "RECHAZADA", "ANULADA"),
      allowNull: false,
      defaultValue: "PENDIENTE",
    },
    motivo_rechazo: {
      type: DataTypes.STRING(255),
      allowNull: true,
    },
  },
  {
    sequelize,
    modelName: "Enrollment",
    tableName: "enrollments",
    indexes: [
      {
        unique: true,
        fields: ["student_id", "codigo_curso"],
        name: "unique_student_codigo_curso",
      },
    ],
  }
);

module.exports = Enrollment;
