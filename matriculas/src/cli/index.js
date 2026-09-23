/**
 * CLI de administracion de Matriculas.
 *
 * Simula el sistema legado tal como lo describe el enunciado: una
 * herramienta de uso interno con la que el personal de la institucion
 * registra estudiantes y los matricula, conectada directo a su propia
 * base de datos, sin ninguna API ni conocimiento del sistema Cupos.
 *
 * Matriculas no tiene ninguna nocion propia de "curso" - al matricular
 * solo se pide un codigo de asignatura en texto libre (ver
 * src/models/Enrollment.js).
 *
 * Se ejecuta como proceso interactivo (no como script que corre y termina).
 */
require("dotenv").config();
const readline = require("node:readline/promises");
const { stdin: input, stdout: output } = require("node:process");
const { sequelize } = require("../models");

const studentsActions = require("./actions/students");
const enrollmentsActions = require("./actions/enrollments");

const rl = readline.createInterface({ input, output });

async function pregunta(texto) {
  const respuesta = await rl.question(texto);
  return respuesta.trim();
}

function imprimirTabla(filas) {
  if (!filas.length) {
    console.log("  (sin resultados)");
    return;
  }
  console.table(filas.map((f) => (f.toJSON ? f.toJSON() : f)));
}

const MENU = `
=========================================
  MATRICULAS - AprendeMas CFT (uso interno)
=========================================
 1) Registrar estudiante
 2) Buscar estudiante por RUT
 3) Matricular estudiante en una asignatura
 4) Ver matriculas de un estudiante
 5) Anular una matricula
 6) Ver cantidad de matriculados por asignatura
 0) Salir
-----------------------------------------
`;

async function accionRegistrarEstudiante() {
  const rut = await pregunta("RUT: ");
  const nombre = await pregunta("Nombre: ");
  const apellido = await pregunta("Apellido: ");
  const email = await pregunta("Email: ");
  const telefono = await pregunta("Telefono (opcional): ");
  const estudiante = await studentsActions.registrarEstudiante({
    rut,
    nombre,
    apellido,
    email,
    telefono: telefono || null,
  });
  console.log(`\nEstudiante registrado con id ${estudiante.id}`);
}

async function accionBuscarEstudiante() {
  const rut = await pregunta("RUT a buscar: ");
  const estudiante = await studentsActions.buscarPorRut(rut);
  if (!estudiante) {
    console.log("No se encontro un estudiante con ese RUT.");
    return;
  }
  imprimirTabla([estudiante]);
}

async function accionMatricular() {
  const rut = await pregunta("RUT del estudiante: ");
  const codigo = await pregunta("Codigo de la asignatura (ej. PY-101): ");

  const { student, enrollment } = await enrollmentsActions.matricularEstudiante({
    rutEstudiante: rut,
    codigoCurso: codigo,
  });
  console.log(
    `\nMatricula ${enrollment.estado}: ${student.nombre} ${student.apellido} -> ${codigo}`
  );
  console.log(
    "(nota: el sistema no valida si esa asignatura existe ni si tiene cupos - eso vive en Cupos, no aqui)"
  );
}

async function accionVerMatriculas() {
  const rut = await pregunta("RUT del estudiante: ");
  const estudiante = await enrollmentsActions.matriculasDeEstudiante(rut);
  const filas = estudiante.matriculas.map((m) => ({
    codigo_curso: m.codigo_curso,
    estado: m.estado,
  }));
  imprimirTabla(filas);
}

async function accionAnularMatricula() {
  const rut = await pregunta("RUT del estudiante: ");
  const codigo = await pregunta("Codigo de la asignatura: ");
  await enrollmentsActions.anularMatricula({ rutEstudiante: rut, codigoCurso: codigo });
  console.log("\nMatricula anulada.");
}

async function accionMatriculasPorCurso() {
  const resultado = await enrollmentsActions.matriculasPorCurso();
  imprimirTabla(resultado);
}

async function main() {
  await sequelize.authenticate();
  await sequelize.sync();
  console.log("Conectado a la base de datos de Matriculas.");

  let salir = false;
  while (!salir) {
    console.log(MENU);
    const opcion = await pregunta("Elige una opcion: ");

    try {
      switch (opcion) {
        case "1":
          await accionRegistrarEstudiante();
          break;
        case "2":
          await accionBuscarEstudiante();
          break;
        case "3":
          await accionMatricular();
          break;
        case "4":
          await accionVerMatriculas();
          break;
        case "5":
          await accionAnularMatricula();
          break;
        case "6":
          await accionMatriculasPorCurso();
          break;
        case "0":
          salir = true;
          break;
        default:
          console.log("Opcion invalida.");
      }
    } catch (error) {
      console.log(`\n[ERROR] ${error.message}`);
    }
  }

  rl.close();
  await sequelize.close();
  console.log("Sesion finalizada.");
  process.exit(0);
}

main().catch((error) => {
  console.error("Error fatal en el CLI de Matriculas:", error);
  process.exit(1);
});
