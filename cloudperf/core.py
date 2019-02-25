from __future__ import absolute_import
import importlib
import pkgutil
import cloudperf.providers
import cachetools
import pandas as pd

prices_url = 'https://cloudperf-data.s3-us-west-2.amazonaws.com/prices.json.gz'
performance_url = 'https://cloudperf-data.s3-us-west-2.amazonaws.com/performance.json.gz'


def sftp_write_file(sftp, name, contents, mode=0o755):
    f = sftp.open(name, 'w')
    f.write(contents)
    f.close()
    if mode is not None:
        sftp.chmod(name, mode)


class DictQuery(dict):
    def get(self, keys, default=None):
        val = None

        for key in keys:
            if val:
                if isinstance(val, list):
                    val = [v.get(key, default) if v else None for v in val]
                else:
                    try:
                        val = val.get(key, default)
                    except AttributeError:
                        return default
            else:
                val = dict.get(self, key, default)

            if val == default:
                break

        return val


@cachetools.cached(cache={})
def get_providers():
    prov_path = cloudperf.providers.__path__
    providers = []

    for _, name, _ in pkgutil.iter_modules(prov_path):
        m = importlib.import_module(name='.{}'.format(name), package='cloudperf.providers')
        if getattr(m, 'CloudProvider', None):
            providers.append(m.CloudProvider())
    return providers


@cachetools.cached(cache={})
def get_prices(prices=None, update=False):
    # if we got a stored file and update is True, merge the two by overwriting
    # old data with new (and leaving not updated old data intact)
    if prices and update:
        old = pd.read_json(prices, orient='records')
        new = pd.concat([cp.get_prices() for cp in get_providers()], ignore_index=True, sort=False)
        if new.empty:
            return old
        # update rows which have the same values in the following columns
        indices = ['provider', 'instanceType', 'region', 'spot', 'spot-az']
        return new.set_index(indices).combine_first(old.set_index(indices)).reset_index()
    if prices:
        return pd.read_json(prices, orient='records')
    return pd.concat([cp.get_prices() for cp in get_providers()], ignore_index=True, sort=False)


@cachetools.cached(cache={})
def get_performance(prices=None, perf=None, update=False, expire=False):
    # if we got a stored file and update is True, merge the two by overwriting
    # old data with new (and leaving not updated old data intact).
    # if expire is set only update old data if the expiry period is passed
    if perf and update:
        old = pd.read_json(perf, orient='records')
        new = pd.concat([cp.get_performance(get_prices(prices), old, update, expire) for cp in get_providers()],
                        ignore_index=True, sort=False)
        if new.empty:
            return old
        # update rows which have the same values in the following columns
        indices = ['provider', 'instanceType', 'benchmark_id', 'benchmark_cpus']
        return new.set_index(indices).combine_first(old.set_index(indices)).reset_index()
    if perf:
        return pd.read_json(perf, orient='records')
    return pd.concat([cp.get_performance(get_prices(prices)) for cp in get_providers()], ignore_index=True, sort=False)


@cachetools.cached(cache={})
def get_combined(prices=prices_url, perf=performance_url):
    prices_df = get_prices(prices=prices)
    perf_df = get_performance(prices=prices, perf=perf)
    combined_df = perf_df.merge(prices_df, how='left', on=['provider', 'instanceType'], suffixes=('', '_prices'))
    combined_df['perf/price/cpu'] = combined_df['benchmark_score']/combined_df['price']/combined_df['benchmark_cpus']

    return combined_df


def terminate_instances():
    for cp in get_providers():
        cp.terminate_instances()
