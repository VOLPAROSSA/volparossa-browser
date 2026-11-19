/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef VolparossaGrantFile_h
#define VolparossaGrantFile_h

#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>

namespace volparossa {

inline constexpr size_t kGrantLimit = 4096;
using GrantBytes = std::array<uint8_t, kGrantLimit + 1>;

inline bool ReadGrantDescriptor(int fd, const char* path, GrantBytes& bytes,
                                size_t& length) {
  struct stat info;
  if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) ||
      (info.st_mode & 07777) != 0600 || info.st_uid != geteuid() ||
      info.st_nlink != 1 || info.st_size <= 0 ||
      info.st_size > static_cast<off_t>(kGrantLimit)) {
    return false;
  }
  // Check the path of the opened object, including any symlinked ancestors.
  char procPath[64];
  std::snprintf(procPath, sizeof(procPath), "/proc/self/fd/%d", fd);
  char resolved[PATH_MAX];
  ssize_t size = readlink(procPath, resolved, sizeof(resolved));
  if (size < 0 || static_cast<size_t>(size) >= sizeof(resolved) ||
      static_cast<size_t>(size) != std::strlen(path) ||
      std::memcmp(resolved, path, static_cast<size_t>(size)) != 0) {
    return false;
  }
  size_t used = 0;
  while (used < bytes.size()) {
    ssize_t n = read(fd, bytes.data() + used, bytes.size() - used);
    if (n == 0) break;
    if (n < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    used += static_cast<size_t>(n);
  }
  if (used != static_cast<size_t>(info.st_size)) return false;
  length = used;
  return true;
}

inline bool ReadPrivateGrant(const char* path, GrantBytes& bytes, size_t& length) {
  int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
  if (fd < 0) return false;
  bool ok = ReadGrantDescriptor(fd, path, bytes, length);
  close(fd);
  return ok;
}

}  // namespace volparossa
#endif
