#include <math.h>
#include <stdlib.h>
#include <string.h>

static unsigned char map_rotate(unsigned char value, int bits) {
    int shift = (bits + 4) % 8;
    return (unsigned char)(((value << shift) | (value >> shift)) & 0xFF);
}

static unsigned char map_get_mask(const unsigned char *key, size_t key_len, long long offset) {
    if (key_len == 0) {
        return 0;
    }
    if (offset > 0x7FFF) {
        offset %= 0x7FFF;
    }
    if (offset < 0) {
        offset = ((offset % 0x7FFF) + 0x7FFF) % 0x7FFF;
    }
    size_t index = (size_t)(((offset * offset) + 71214) % (long long)key_len);
    return map_rotate(key[index], index & 0x07);
}

static int map_decrypt(const unsigned char *key, size_t key_len, unsigned char *buffer, size_t buffer_len, long long offset) {
    for (size_t index = 0; index < buffer_len; index++) {
        buffer[index] ^= map_get_mask(key, key_len, offset + index);
    }
    return 1;
}

static unsigned int rc4_compute_hash(const unsigned char *key, size_t key_len) {
    unsigned int value = 1;
    for (size_t index = 0; index < key_len; index++) {
        if (key[index] == 0) {
            continue;
        }
        unsigned int next_value = value * (unsigned int)key[index];
        if (next_value == 0 || next_value <= value) {
            break;
        }
        value = next_value;
    }
    return value;
}

static int rc4_segment_skip(const unsigned char *key, size_t key_len, unsigned int hash, long long segment_id) {
    long long key_len_ll = (long long)key_len;
    int key_index = (int)(segment_id % key_len_ll);
    if (key_index < 0) {
        key_index += (int)key_len;
    }
    long long seed = (long long)key[key_index];
    if (seed == 0) {
        return 0;
    }
    long long index = (long long)((double)hash / (double)((segment_id + 1) * seed) * 100.0);
    int result = (int)(index % key_len_ll);
    return result < 0 ? result + (int)key_len : result;
}

static int rc4_decrypt(const unsigned char *key, size_t key_len, unsigned char *buffer, size_t buffer_len, long long offset) {
    int *box = (int *)malloc((size_t)key_len * sizeof(int));
    if (box == NULL) {
        return 0;
    }
    for (size_t index = 0; index < key_len; index++) {
        box[index] = index & 0xFF;
    }
    size_t cursor = 0;
    for (size_t index = 0; index < key_len; index++) {
        cursor = (cursor + box[index] + key[index]) % key_len;
        int tmp = box[index];
        box[index] = box[cursor];
        box[cursor] = tmp;
    }

    unsigned int hash = rc4_compute_hash(key, key_len);
    const int first_segment_size = 128;
    const int segment_size = 5120;
    size_t processed = 0;
    long long position = offset;

    if (position < first_segment_size) {
        size_t block_size = buffer_len < (size_t)(first_segment_size - position) ? buffer_len : (size_t)(first_segment_size - position);
        for (size_t index = 0; index < block_size; index++) {
            int skip = rc4_segment_skip(key, key_len, hash, position + index);
            buffer[processed + index] ^= key[skip];
        }
        processed += block_size;
        position += block_size;
    }

    while (processed < buffer_len) {
        int segment_offset = (int)(position % segment_size);
        if (segment_offset < 0) {
            segment_offset += segment_size;
        }
        size_t block_size = (size_t)(segment_size - segment_offset);
        if (block_size > buffer_len - processed) {
            block_size = buffer_len - processed;
        }

        int *box_copy = (int *)malloc((size_t)key_len * sizeof(int));
        if (box_copy == NULL) {
            free(box);
            return 0;
        }
        memcpy(box_copy, box, (size_t)key_len * sizeof(int));

        int cursor_a = 0;
        int cursor_b = 0;
        int skip_len = segment_offset + rc4_segment_skip(key, key_len, hash, position / segment_size);
        for (int index = -skip_len; index < (int)block_size; index++) {
            cursor_a = (cursor_a + 1) % key_len;
            cursor_b = (box_copy[cursor_a] + cursor_b) % key_len;
            int tmp = box_copy[cursor_a];
            box_copy[cursor_a] = box_copy[cursor_b];
            box_copy[cursor_b] = tmp;
            if (index >= 0) {
                int mask_index = (box_copy[cursor_a] + box_copy[cursor_b]) % key_len;
                buffer[processed + index] ^= (unsigned char)box_copy[mask_index];
            }
        }
        free(box_copy);
        processed += block_size;
        position += block_size;
    }
    free(box);
    return 1;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int qmc2_decrypt(const unsigned char *key, size_t key_len, unsigned char *buffer, size_t buffer_len, long long offset) {
    if (key == NULL || buffer == NULL || key_len == 0 || key_len > 8192) {
        return 0;
    }
    if (buffer_len == 0) {
        return 1;
    }
    if (key_len > 300) {
        return rc4_decrypt(key, key_len, buffer, buffer_len, offset);
    }
    return map_decrypt(key, key_len, buffer, buffer_len, offset);
}
