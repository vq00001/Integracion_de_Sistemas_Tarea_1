require("dotenv").config();
const { sequelize, Student, Enrollment } = require("../models");

/**
 * Datos de ejemplo. Matriculas no tiene tabla de cursos: los codigos de
 * asignatura usados aqui son solo texto libre, elegido para que la demo
 * se sienta realista - no hay ninguna referencia real hacia Cupos.
 */
async function seed() {
  await sequelize.sync({ force: false });

  const estudiantes = await Student.bulkCreate(
    [
      { rut: "11111111-1", nombre: "Camila", apellido: "Rojas", email: "camila.rojas@example.com" },
      { rut: "22222222-2", nombre: "Matias", apellido: "Fuentes", email: "matias.fuentes@example.com" },
    ],
    { ignoreDuplicates: true }
  );

  const camila = await Student.findOne({ where: { rut: "11111111-1" } });
  const matriculasEjemplo = camila
    ? await Enrollment.bulkCreate(
        [
          { student_id: camila.id, codigo_curso: "PY-101", estado: "CONFIRMADA" },
          { student_id: camila.id, codigo_curso: "BD-101", estado: "CONFIRMADA" },
        ],
        { ignoreDuplicates: true }
      )
    : [];

  console.log(
    `Seed completado: ${estudiantes.length} estudiantes, ${matriculasEjemplo.length} matriculas de ejemplo.`
  );
  process.exit(0);
}

seed().catch((err) => {
  console.error("Error en seed:", err);
  process.exit(1);
});
