-- Consolidate the studio board from 17 roles to 7 working roles plus boss (Producer, Researcher, Writer, Editor,
-- Designer, Post-Production, Publisher). Only touches public.studio_board.
delete from public.studio_board;
insert into public.studio_board (id, role, model, home_room, room) values
  ('role-producer', 'producer', 'AI model', 'newsroom', 'newsroom'),
  ('role-researcher', 'researcher', 'AI model + data', 'newsroom', 'newsroom'),
  ('role-writer', 'writer', 'AI model', 'newsroom', 'newsroom'),
  ('role-editor', 'editor', 'AI model', 'standards_desk', 'standards_desk'),
  ('role-designer', 'designer', 'AI model', 'production_floor', 'production_floor'),
  ('role-post_production', 'post_production', 'automated code', 'production_floor', 'production_floor'),
  ('role-publisher', 'publisher', 'AI model', 'publishing_suite', 'publishing_suite'),
  ('role-boss', 'boss', 'you + Claude Code', 'exec_suite', 'exec_suite');
