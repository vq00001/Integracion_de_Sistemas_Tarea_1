const { Student, Enrollment } = require("../../models");

/**
 * IMPORTANTE - esto reproduce a proposito el problema descrito en el
 * enunciado: "se matricula sin verificar cupos y algunos cursos superan
 * la capacidad de la sala".
 *
 * Matriculas no tiene ninguna nocion de "curso" propia: codigoCurso es
 * un texto libre que quien matricula escribe. No se valida contra
 * ninguna tabla local (no existe) ni contra Cupos (no hay integracion
 * todavia). Solo se valida la regla que si le corresponde a Matriculas:
 * que el estudiante exista y no este ya matriculado en ese mismo codigo.
 */
async function matricularEstudiante({ rutEstudiante, codigoCurso }) {
  const student = await Student.findOne({ where: { rut: rutEstudiante } });
  if (!student) {
    throw new Error(`No existe un estudiante con RUT ${rutEstudiante}`);
  }

  const yaExiste = await Enrollment.findOne({
    where: { student_id: student.id, codigo_curso: codigoCurso },
  });
  if (yaExiste) {
    throw new Error("El estudiante ya esta matriculado en esa asignatura");
  }

  const enrollment = await Enrollment.create({
    student_id: student.id,
    codigo_curso: codigoCurso,
    estado: "CONFIRMADA",
  });

  return { enrollment, student };
}

async function anularMatricula({ rutEstudiante, codigoCurso }) {
  const student = await Student.findOne({ where: { rut: rutEstudiante } });
  if (!student) throw new Error(`No existe un estudiante con RUT ${rutEstudiante}`);

  const enrollment = await Enrollment.findOne({
    where: { student_id: student.id, codigo_curso: codigoCurso },
  });
  if (!enrollment) throw new Error("No existe esa matricula");

  enrollment.estado = "ANULADA";
  await enrollment.save();
  return enrollment;
}

async function matriculasDeEstudiante(rutEstudiante) {
  const student = await Student.findOne({
    where: { rut: rutEstudiante },
    include: [{ association: "matriculas" }],
  });
  if (!student) throw new Error(`No existe un estudiante con RUT ${rutEstudiante}`);
  return student;
}

/**
 * Conteo simple de matriculas confirmadas por codigo de asignatura. No
 * compara contra ninguna capacidad (Matriculas no la conoce).
 */
async function matriculasPorCurso() {
  const todas = await Enrollment.findAll({ where: { estado: "CONFIRMADA" } });
  const conteo = {};
  for (const m of todas) {
    conteo[m.codigo_curso] = (conteo[m.codigo_curso] || 0) + 1;
  }
  return Object.entries(conteo).map(([codigo_curso, matriculados]) => ({
    codigo_curso,
    matriculados,
  }));
}

module.exports = {
  matricularEstudiante,
  anularMatricula,
  matriculasDeEstudiante,
  matriculasPorCurso,
};
