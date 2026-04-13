class LidGuard < Formula
  desc "Keep your laptop awake on lid close while selected coding agents are running"
  homepage "https://github.com/JasonLeviGoodison/AlwaysGrinding"
  license "MIT"
  head "https://github.com/JasonLeviGoodison/AlwaysGrinding.git", branch: "main"

  depends_on "python@3.12"

  def install
    system Formula["python@3.12"].opt_bin/"python3", "scripts/build_zipapp.py"
    bin.install "dist/lid-guard.pyz" => "lid-guard"
  end

  test do
    assert_match "lid-guard", shell_output("#{bin}/lid-guard --version")
  end
end

