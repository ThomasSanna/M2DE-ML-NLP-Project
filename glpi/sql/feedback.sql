-- User feedback per ticket, from the GLPI satisfaction surveys.
-- Source of the w1 factor (user_feedback) of the memory score.
-- One row per closed ticket that has a survey (answered or not).
SELECT
    t.id                                   AS ticket_id,
    t.name                                 AS title,
    c.completename                         AS category,
    t.content                              AS question,
    sol.content                            AS solution,
    sol.status                             AS solution_status,   -- 2 = pending, 3 = accepted, 4 = refused
    t.solvedate                            AS solved_at,
    t.closedate                            AS closed_at,
    s.date_begin                           AS survey_sent_at,
    s.date_answered                        AS survey_answered_at,
    s.satisfaction                         AS score,             -- 0..5, NULL = no answer
    s.satisfaction_scaled_to_5 / 5         AS normalized_feedback, -- 0..1, value of w1
    s.comment                              AS comment
FROM glpi_tickets t
JOIN glpi_ticketsatisfactions s  ON s.tickets_id = t.id
LEFT JOIN glpi_itilcategories c  ON c.id = t.itilcategories_id
LEFT JOIN glpi_itilsolutions sol ON sol.id = (
    SELECT MAX(id) FROM glpi_itilsolutions
    WHERE itemtype = 'Ticket' AND items_id = t.id
)
WHERE t.is_deleted = 0
ORDER BY t.id;
