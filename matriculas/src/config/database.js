const { Sequelize } = require("sequelize");
require("dotenv").config();

const sequelize = new Sequelize(
  process.env.DB_NAME || "matriculas_db",
  process.env.DB_USER || "matriculas_user",
  process.env.DB_PASSWORD || "matriculas_pass",
  {
    host: process.env.DB_HOST || "db",
    port: process.env.DB_PORT || 5432,
    dialect: "postgres",
    logging: process.env.NODE_ENV === "development" ? console.log : false,
    define: {
      underscored: true,
      timestamps: true,
    },
  }
);

module.exports = sequelize;
