/**
 * Punto de entrada base del proyecto Matriculas.
 *
 * Este proyecto, por ahora, NO expone una API. Su responsabilidad es:
 *   1) Definir el modelo de datos de Matriculas (students, enrollments).
 *   2) Conectarse a su propia base de datos PostgreSQL y mantenerla sincronizada.
 *
 * Matriculas no tiene tabla de cursos (ver README, seccion "Matriculas
 * no tiene tabla de cursos") - Enrollment guarda codigo_curso como texto
 * libre, sin relacion a ninguna otra tabla.
 */
require("dotenv").config();
const { sequelize } = require("./models");

async function main() {
  try {
    await sequelize.authenticate();
    console.log("Conexion a la base de datos de Matriculas establecida.");

    // Crea/actualiza las tablas segun los modelos definidos en src/models.
    // Para un entorno productivo, reemplazar por migraciones versionadas.
    await sequelize.sync();
    console.log("Esquema de Matriculas sincronizado (students, enrollments).");
    console.log("Base de datos lista. Aun no hay API expuesta.");
  } catch (error) {
    console.error("No se pudo inicializar la base de datos de Matriculas:", error);
    process.exit(1);
  }
}

main();
