const { Student } = require("../../models");

async function registrarEstudiante({ rut, nombre, apellido, email, telefono }) {
  const existente = await Student.findOne({ where: { rut } });
  if (existente) {
    throw new Error(`Ya existe un estudiante registrado con RUT ${rut}`);
  }
  return Student.create({ rut, nombre, apellido, email, telefono });
}

async function buscarPorRut(rut) {
  return Student.findOne({ where: { rut } });
}

async function listarEstudiantes() {
  return Student.findAll({ order: [["apellido", "ASC"]] });
}

module.exports = { registrarEstudiante, buscarPorRut, listarEstudiantes };
