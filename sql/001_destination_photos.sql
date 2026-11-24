-- =====================================================================
-- NUSAWIS - tabel foto destinasi wisata
--
-- Cara pakai: buka di DBeaver (koneksi database "nusawis"),
--             lalu Execute SQL Script (Alt+X).
-- Aman dijalankan berulang kali (IF NOT EXISTS).
--
-- Satu destinasi boleh punya banyak foto. Foto dengan sort_order
-- terkecil dipakai sebagai foto sampul (cover) di kartu home.
-- =====================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS destination_photos (
    id             BIGSERIAL    PRIMARY KEY,
    destination_id UUID         NOT NULL REFERENCES destinations (id) ON DELETE CASCADE,
    url            VARCHAR(500) NOT NULL,
    caption        VARCHAR(150),
    sort_order     INTEGER      NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_destination_photos_destination_url UNIQUE (destination_id, url),
    CONSTRAINT ck_destination_photos_url_http CHECK (url LIKE 'http://%' OR url LIKE 'https://%')
);

-- Ambil foto sampul / galeri per destinasi tanpa scan seluruh tabel
CREATE INDEX IF NOT EXISTS ix_destination_photos_destination
    ON destination_photos (destination_id, sort_order, id);

COMMENT ON TABLE destination_photos IS 'Foto destinasi wisata. sort_order terkecil = foto sampul (cover).';
COMMENT ON COLUMN destination_photos.url IS 'URL gambar, wajib diawali http:// atau https://';
COMMENT ON COLUMN destination_photos.caption IS 'Keterangan / alt text foto (opsional)';
COMMENT ON COLUMN destination_photos.sort_order IS 'Urutan tampil, 0 = foto sampul';

COMMIT;


-- ---------------------------------------------------------------------
-- Contoh mengisi foto (ganti slug dan URL), jalankan setelah data
-- destinasi ada. Hapus tanda -- di depan baris untuk menjalankan.
-- ---------------------------------------------------------------------
-- INSERT INTO destination_photos (destination_id, url, caption, sort_order)
-- SELECT id, 'https://contoh.com/foto/batu-bolong-1.jpg', 'Pantai Batu Bolong saat senja', 0
-- FROM destinations
-- WHERE slug = 'pantai-batu-bolong'
-- ON CONFLICT (destination_id, url) DO NOTHING

-- ---------------------------------------------------------------------
-- Membatalkan (menghapus tabel beserta semua fotonya):
-- ---------------------------------------------------------------------
-- DROP TABLE IF EXISTS destination_photos
