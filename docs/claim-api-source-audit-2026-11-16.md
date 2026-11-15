# Audit API Claim Resmi LUMAVUN (16 November 2026, WITA)

**Temuan:** Belum ada penyedia dalam daftar audit yang memenuhi syarat
**claim peserta, Solana mainnet, modal Rp0, tanpa wallet signing,
diizinkan otomatisasi, dan API resmi yang cocok dengan kontrak V1.6**.

Audit ini memverifikasi *isi dokumentasi publik*, bukan akses terhadap akun
peserta, bukti eligibility wallet, atau ketersediaan kampanye tertentu.
Tidak ada API claim yang dipanggil dan tidak ada reward diterima karena audit.
Metadata terstruktur: [`config/claim_api_research.json`](../config/claim_api_research.json).

## Temuan per penyedia

| Penyedia | Kemampuan yang didokumentasikan | Mengapa tidak diaktifkan |
|---|---|---|
| Solana [requestAirdrop](https://solana.com/developers/cookbook/development/airdrops-and-faucets) | RPC faucet Devnet/Testnet, terprogram | Bukan Mainnet, token uji bukan pendapatan |
| [thirdweb Distribute Tokens](https://portal.thirdweb.com/transactions/distribute-tokens) | Distribusi token dengan gas disponsori | Membutuhkan server wallet berisi token dan kunci proyek penerbit; bukan API claim terbuka milik peserta |
| [TON Console Jetton Airdrop](https://docs.tonapi.io/tonconsole/jettons/airdrop) | API baca klaim untuk dApp dan transaksi claim | Claim memerlukan tanda tangan wallet dan biaya TON; bukan Solana Mainnet |
| [Helium Rewards](https://docs.helium.com/network-data/solana/rewardable-entities/) | Oracle REST untuk besaran reward dan SDK transaksi | Memerlukan kepemilikan Hotspot, tanda tangan transaksi Solana serta fee; [biaya resmi](https://docs.helium.com/tokens/sol-token/) |
| [LayerHop claims checker](https://docs.layerhop.com/api-reference) | API publik cek claim yang sudah/pernah tersedia | Hanya read-only, bukan API claim untuk peserta; informasi ini perlu dikonfirmasi kembali bila dokumen berubah |
| [Solana Actions](https://solana.com/docs/tools/actions) | API menyusun transaksi untuk ditandatangani | Bukan jalur claim tanpa tanda tangan |
| [Galxe REST Credential](https://help.galxe.com/en/articles/10242805-rest-api-management) | API *Space Owner* memeriksa kelayakan task | Bukan izin umum menjalankan claim peserta. [Panduan fee](https://help.galxe.com/en/articles/12829115-understanding-claim-fees) menyebut biaya claim |

| [Streamflow Airdrop SDK](https://docs.streamflow.finance/en/articles/9675545-distributor-sdk) | SDK `claim` untuk penerima distribusi Solana | [Panduan claim](https://docs.streamflow.finance/en/articles/14825485-how-to-claim-your-tokens-on-streamflow) memerlukan konfirmasi wallet. [Jadwal biaya](https://docs.streamflow.finance/en/articles/17560817-costs-of-using-streamflow) mencantumkan claim 0,0237–0,0317 SOL |
| [Solana Kora](https://solana.com/docs/tools/kora/getting-started) | Infrastruktur paymaster untuk mensponsori biaya transaksi | Sponsorship perlu disediakan dan didanai proyek. Bukan airdrop, bukan bukti eligibility wallet, dan tidak otomatis memberi reward |
| [Solana Rewards Program](https://solana.com/docs/tools/rewards) | Protokol distribusi token dan Merkle claim | Protokol umum tanpa kampanye eligible yang diketahui; claim melalui instruksi onchain, bukan REST claim peserta gratis yang sudah diverifikasi |

## Pengamatan teknis tambahan

Biaya yang disponsori **tidak sama** dengan izin melakukan claim otomatis.
Kora memungkinkan sebuah proyek membiayai transaksi pengguna, tetapi bot
LUMAVUN tetap harus memiliki kampanye tertentu, reward riil, bukti peserta
eligible, persetujuan dan metode claim resmi. Solana Rewards Program juga
menjelaskan Merkle proof dan distribusi, bukan membagikan token gratis kepada
sembarang wallet.

Semua temuan ini dimasukkan ke kategori API/protokol terpisah sehingga
`CLAIM_AUTO_ENABLED` tidak dapat mengubahnya menjadi adapter claim peserta.

## Perubahan penting pada Galxe

Menurut [pengumuman resmi 9 November 2026](https://help.galxe.com/en/articles/18361711-galxe-is-integrating-into-gravity-what-you-need-to-know), Galxe Quest dan sejumlah layanan terkait akan dihentikan bertahap:

- Mulai **9 November 2026**, kampanye baru tidak lagi dapat dibuat.
- **5 Desember 2026** adalah batas partisipasi quest.
- **15 Desember 2026** adalah batas klaim reward dari quest; website menjadi read-only.

Ini adalah batas platform, **bukan bukti wallet tertentu memenuhi syarat claim**.
Agent tidak boleh menjadikan berita tentang penghentian layanan sebagai
alasan mengklaim token secara otomatis.

## Integrasi ke V1.6

- `src/claim_api_audit.py` memvalidasi sumber penelitian, status, tanggal,
  alasan pengecualian dan daftar kandidat yang memperoleh review positif.
- `scripts/report_claim_api_research.py` membuat
  `claim_api_research_report.json` di artifact `latest_report` setiap scan.
- `claim_engine.adapters()` kini menolak registrasi adapter yang tidak
  memiliki bukti persetujuan **terpisah** di daftar sumber resmi, bahkan
  bila seseorang menandai semua boolean di adapter sebagai `true`.
- Data audit tidak melakukan panggilan HTTP ke endpoint claim,
  tidak mengakses token GitHub/Cloudflare, dan tidak mengubah wallet.
- Setelah lebih dari tujuh hari sejak tanggal audit, laporan memberi
  tanda `stale_review=true` agar bukti tidak dianggap masih mutakhir.

**Belum ada adapter aktif.** `config/claim_adapters.json` tetap kosong,
dan `CLAIM_AUTO_ENABLED` tidak perlu dinyalakan sampai ada kampanye
yang benar-benar lolos.

## Syarat menambahkan integrasi claim nyata

1. Ada kampanye resmi tertentu, ID kanonik dari antrean, tanggal masih aktif,
   peserta dan wallet memenuhi syarat.
2. Endpoint claim resmi **untuk peserta** disertai dokumentasi kontrak
   request/response dan pernyataan eksplisit bahwa otomatisasi diizinkan.
3. Bukti resmi bahwa biaya *partisipasi*, *claim*, dan *gas* adalah nol;
   relayer/server wallet sponsor harus dikonfirmasi, bukan diasumsikan.
4. Tidak meminta private key/seed phrase, tidak memerlukan wallet signing
   tersembunyi, tidak melakukan login/CAPTCHA bypass, tidak melakukan
   social automation melanggar aturan.
5. Review positif di `claim_api_research.json` berisi sumber bukti spesifik
   untuk izin otomatisasi, gas/biaya, eligibility, tanggal review, dan
   host API. Adapter V1.6 harus cocok persis dengan review tersebut.
6. Lakukan uji kontrak terisolasi terlebih dahulu; eksekusi produksi
   hanya setelah persetujuan per kampanye melalui workflow GitHub yang
   memang sudah disediakan.

Laporan real-time bukan berarti semua biaya atau kelayakan telah diteliti.
Sistem sengaja mengembalikan **NO_APPROVED_CLAIM_API** ketimbang menebak
endpoint dari tombol `Claim` di browser.

Sumber awal diperiksa pada 15 November 2026 UTC (16 November WITA),
dan tiga sumber tambahan diperiksa pada 16 November 2026 WITA.
Tidak ada sumber yang dipakai untuk melakukan POST claim.
