# Tarea 1 Integración de Sistemas

La Centro de Formación Técnica AprendeMás opera hoy con dos sistemas que nacieron por separado y nunca conversaron entre sí:

- Cupos: un sistema que administra los cursos y los cupos disponibles de cada uno. Fue desarrollado internamente y su equipo lo mantiene activamente. Es donde vive la verdad sobre qué cursos se dictan y cuántos cupos quedan en cada uno.
- Matrículas: un sistema que registra a los estudiantes y sus matrículas. Necesita saber, para cada curso, si hay cupos disponibles antes de matricular a un estudiante, y consulta esa información con muchísima frecuencia. Hoy los dos sistemas no están integrados: se matricula sin verificar cupos y algunos cursos superan la capacidad de la sala.

Este proyecto implementa una solución de integración para el Sistema de Gestión de Cursos y Matrículas del AprendeMás, y prueba la resiliencia de esta a través de experimentos.
