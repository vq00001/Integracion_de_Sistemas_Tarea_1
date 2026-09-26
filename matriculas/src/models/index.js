const sequelize = require("../config/database");
const Student = require("./Student");
const Enrollment = require("./Enrollment");

// --- Relaciones ---
// No hay modelo Course: Matriculas no es dueña del catalogo de cursos
// (ver Enrollment.js). Solo existe la relacion Student <-> Enrollment.
Student.hasMany(Enrollment, { foreignKey: "student_id", as: "matriculas" });
Enrollment.belongsTo(Student, { foreignKey: "student_id", as: "estudiante" });

module.exports = {
  sequelize,
  Student,
  Enrollment,
};
