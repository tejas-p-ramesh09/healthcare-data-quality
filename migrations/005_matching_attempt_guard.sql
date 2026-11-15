CREATE TRIGGER matching_attempt_update BEFORE UPDATE ON matching_attempt
FOR EACH ROW EXECUTE FUNCTION matching_result_guard();
