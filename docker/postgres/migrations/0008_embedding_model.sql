-- 0008 -- record which model produced each stored product embedding.
--
-- Vectors from different models (or different dimensions) are not comparable, and until embeddings
-- were decoupled from the chat provider (EMBEDDING_PROVIDER / EMBEDDING_MODEL / EMBEDDING_DIM) nothing
-- on the row said which model wrote it. The seeder's loader and scripts/generate_embeddings.py now
-- fill it; "replay-hashing-vectorizer" marks the offline hashing vectors of LLM_PROVIDER=replay.
--
-- Nullable: NULL means "written before this migration, model unknown". The vector column's width is
-- deliberately NOT touched here -- changing it destroys the stored vectors, so it is an explicit
-- operator action: scripts/reset_embeddings.py --dim N.

ALTER TABLE product_embeddings ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(200);
