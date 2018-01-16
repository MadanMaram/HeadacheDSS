# Standard Library
from collections import Counter
import pickle
import os

# NumPy and Pandas
import pandas as pd
import numpy as np

# Sci-kit learn
from sklearn import tree
from sklearn.metrics import (accuracy_score, f1_score,
                             classification_report, cohen_kappa_score)
from sklearn.model_selection import StratifiedKFold

# Sci-kit contrib imblearn (for over-sampling techniques)
from imblearn.over_sampling import SMOTE, ADASYN

# rdflib for RDF parsing and processing
from rdflib import Graph

# Our semantic processor
from SemanticProcessor import encoder, decoder, generator

# Surpress warnings
import warnings
warnings.filterwarnings('ignore')

# Read the data
migbase = pd.read_csv('data/migbase_encoded.csv').drop('Unnamed: 0', axis=1)

# Filter out columns with more than 1 unique values
_columns = [col for col in migbase.columns
            if len(np.unique(migbase[col])) > 1]
migbase = migbase[_columns]

# Drop the single sample with class 'no headache'
migbase = migbase[migbase['CLASS'] != 'no headache']


def preprocess(features, labels):
    """Preprocess the feature and label dataframe. Parse all categorical
string variables to integers and map each class/label to an
integer"""

# Dictionary with mappings from string variables to integers
    mapping_per_class = {
        # Duration group is a discretization of the headache duration
            # It is thus an ordinal variable.
            'durationGroup': {
                'A': 0,
                    'B': 1,
                    'C': 2,
                    'D': 3,
                    'E': 4,
                    'F': 5,
                    'G': 6,
                    'H': 7,
                    'I': 8,
                    'J': 9
            },

            # Severity is an ordinal variable as well
            'severity': {
                'mild': 0,
                    'moderate': 1,
                    'severe': 2,
                    'very severe': 3
            },

            # The number of previous similar headache attacks is ordinal
            'previous_attacks': {
                '2-4': 0,
                    '5-9': 1,
                    '10-19': 2,
                    '20': 3
            },

            # One-hot-encoding performs worse than integer encoding
            # for these non-ordinal categorical variables
            'location': {
                'unilateral': 0,
                    'bilateral': 1,
                    'orbital': 2
            },

            'characterisation': {
                'stabbing': 0,
                    'pressing': 1,
                    'pulsating': 2
            }
    }

    # If we find a column with only 'yes' and 'no' as values
    # Then we map 'yes' to 1 and 'no' to 0
    # Else, we use a mapping defined above
    for col in features.columns:
        unique_values = np.unique(features[col])
        if 'no' in unique_values or 'yes' in unique_values:
            features[col] = features[col].map({'no': 0, 'yes': 1})
            mapping_per_class[col] = {'no': 0, 'yes': 1}
        else:
            features[col] = features[col].map(mapping_per_class[col])

    return features, labels.map({'cluster': 0, 'tension': 1, 'migraine': 2})

# Split our data in a features data frame and a labels dataframe
# and preprocess it
features = migbase.drop('CLASS', axis=1)
labels = migbase['CLASS']
features, labels = preprocess(features, labels)

def oversample_SMOTE(X_train, y_train):
    """Wrapper around imblearn.SMOTE to oversample"""
    smote = SMOTE()
    X_train, y_train = smote.fit_sample(X_train, y_train)
    weights = np.array([1.0] * len(y_train))
    return X_train, y_train, weights


def oversample_ADASYN(X_train, y_train):
    """Wrapper around imblearn.ADASYN to oversample"""
    adasyn = ADASYN()
    X_train, y_train = adasyn.fit_sample(X_train, y_train)
    weights = np.array([1.0] * len(y_train))
    return X_train, y_train, weights


def oversample_none(X_train, y_train):
    """Do nothing, just return the X_train, y_train and a
    one-vector of same length as y_train"""
    return X_train, y_train, np.array([1.0] * len(y_train))


def oversample_weighted(X_train, y_train):
    """Give more weight to cluster and tension samples
    depending of the fraction between the specific class and
    the migraine class"""
    cntr = Counter(y_train)
    cluster_weight = cntr[2] / cntr[0]
    tension_weight = cntr[2] / cntr[1]
    weights = {0: cluster_weight, 1: tension_weight, 2: 1.0}
    return X_train, y_train, weights


def oversample_prior_knowledge(X_train, y_train):
    """Use prior knowledge, encoded in ICHD_KB.ttl and headache_KG.ttl
    to generate artifcial samples"""
    # How many cluster and tension samples do we need for a balanced set?
    # majority class = migraine (encoded as 2)
    n_cluster_samples = len(y_train[y_train == 2]) - len(y_train[y_train == 0])
    n_tension_samples = len(y_train[y_train == 2]) - len(y_train[y_train == 1])

    # Generate the samples in the form of RDF-files
    generator.generate_samples(
        'Cluster', ['SemanticProcessor/data/headache_KG.ttl',
                    'SemanticProcessor/data/ICHD_KB.ttl'],
            n=n_cluster_samples, id_offset=1000,
            output_path='SemanticProcessor/data/generated_samples_cluster.ttl'
    )
    generator.generate_samples(
        'Tension', ['SemanticProcessor/data/headache_KG.ttl',
                    'SemanticProcessor/data/ICHD_KB.ttl'],
            n=n_tension_samples, id_offset=2000,
            output_path='SemanticProcessor/data/generated_samples_tension.ttl'
    )

    # Decoded the generated RDF-files to get a pandas dataframe
    new_df = decoder.decode(
        Graph(
        ).parse(
            "SemanticProcessor/data/generated_samples_cluster.ttl",
                format="turtle")
    )

    # Apply the same pre-processing to our new dataframe
    new_features = new_df.drop(['index', 'CLASS'], axis=1)
    new_labels = new_df['CLASS']
    new_features, new_labels = preprocess(new_features, new_labels)
    new_features = new_features.reindex_axis(X_train.columns, axis=1)

    # Append it
    X_train = pd.concat([X_train, new_features])
    y_train = pd.concat([y_train, new_labels])

    new_df = decoder.decode(
        Graph(
        ).parse("SemanticProcessor/data/generated_samples_tension.ttl",
                format="turtle")
    )

    # Apply the same pre-processing to our new dataframe
    new_features = new_df.drop(['index', 'CLASS'], axis=1)
    new_labels = new_df['CLASS']
    new_features, new_labels = preprocess(new_features, new_labels)
    new_features = new_features.reindex_axis(X_train.columns, axis=1)

    # Append it
    X_train = pd.concat([X_train, new_features])
    y_train = pd.concat([y_train, new_labels])

    return X_train, y_train, np.array([1.0] * len(y_train))


N_SIMULATIONS = 100
samplers = {
    'None': oversample_none,
    'SMOTE': oversample_SMOTE,
    'ADASYN': oversample_ADASYN,
    'Prior Knowledge': oversample_prior_knowledge,
    'Sample Weight': oversample_weighted
}

# Create the output directory and subdirectories if needed
if not os.path.exists('output'):
    os.makedirs('output')
if not os.path.exists('output' + os.sep + 'oversampling'):
    os.makedirs('output' + os.sep + 'oversampling')
for sampler in samplers:
    if not os.path.exists('output' + os.sep + 'oversampling' + os.sep + sampler):
        os.makedirs('output' + os.sep + 'oversampling' + os.sep + sampler)


for _ in range(N_SIMULATIONS):
    # Generate a random seed, to make sure every sampler gets the same data
    SEED = np.random.randint(1000000)
    np.random.seed(SEED)
    skf = StratifiedKFold(n_splits=5, random_state=SEED)
    for sampler in samplers:
        preds = np.zeros((len(labels), 3))
        for i, (train_idx, test_idx) in enumerate(skf.split(features, labels)):
            X_train = features.iloc[train_idx, :]
            X_test = features.iloc[test_idx, :]
            y_train = labels.iloc[train_idx]
            y_test = labels.iloc[test_idx]

            X_train, y_train, weights = samplers[sampler](X_train, y_train)

            dt = tree.DecisionTreeClassifier(
                random_state=SEED,
                criterion='entropy',
                class_weight=[None, 'balanced'][sampler == 'Sample Weight'])
            dt.fit(X_train, y_train)
            preds[test_idx, :] = dt.predict_proba(X_test)
        preds_df = pd.DataFrame(
            preds,
            columns=['cluster_prob',
                     'tension_prob',
                     'migraine_prob'])
        preds_df.to_csv('output' + os.sep + 'oversampling' +
                        os.sep + sampler + os.sep + 'preds_' + str(SEED) + '.csv')

"""
# We store the following metrics
cohen_per_sampler = {}
acc_per_sampler = {}
cluster_f1_per_sampler = {}
tension_f1_per_sampler = {}
migraine_f1_per_sampler = {}

samplers = ['None', 'SMOTE', 'ADASYN', 'Prior Knowledge', 'Sample Weight']
for _ in range(100):
    print('-'*100)
    SEED = np.random.randint(1000000)
    skf = StratifiedKFold(n_splits=5, random_state=SEED)
    for sampler in samplers:
        migbase_accs, migbase_f1 = [], []
        chron_accs, chron_f1 = [], []
        cluster_f1s = []
        tension_f1s = []
        migraine_f1s = []
        migbase_cohens = []
        for i, (train_idx, test_idx) in enumerate(skf.split(features, labels)):
            # Split data in training- and test-set
            X_train = features.iloc[train_idx, :]
            X_test = features.iloc[test_idx, :]

            y_train = labels.iloc[train_idx]
            y_test = labels.iloc[test_idx]

            weights = None

            # (Over)sample our training data
            if sampler == 'SMOTE':
                smote = SMOTE(random_state=SEED)
                X_train, y_train = smote.fit_sample(X_train, y_train)
            elif sampler == 'ADASYN':
                adasyn = ADASYN(random_state=SEED)
                X_train, y_train = adasyn.fit_sample(X_train, y_train)
            elif sampler == 'Sample Weight':
                weights = []
                cntr = Counter(y_train)
                cluster_weight = cntr[2]/cntr[0]
                tension_weight = cntr[2]/cntr[1]
                weights = {0: cluster_weight, 1: tension_weight, 2: 1.0}
            elif sampler == 'Prior Knowledge':
                # How many cluster and tension samples do we need for a balanced set?
                # majority class = migraine (encoded as 2)
                n_cluster_samples = len(y_train[y_train == 2]) - len(y_train[y_train == 0])
                n_tension_samples = len(y_train[y_train == 2]) - len(y_train[y_train == 1])

                # Generate the samples in the form of RDF-files
                generator.generate_samples('Cluster', ['SemanticProcessor/data/headache_KG.ttl', 'SemanticProcessor/data/ICHD_KB.ttl'], 
                                           n=n_cluster_samples, id_offset=1000,
                                           output_path='SemanticProcessor/data/generated_samples_cluster.ttl')
                generator.generate_samples('Tension', ['SemanticProcessor/data/headache_KG.ttl', 'SemanticProcessor/data/ICHD_KB.ttl'], 
                                           n=n_tension_samples, id_offset=2000,
                                           output_path='SemanticProcessor/data/generated_samples_tension.ttl')

                # Decoded the generated RDF-files to get a pandas dataframe
                new_df = decoder.decode(Graph().parse("SemanticProcessor/data/generated_samples_cluster.ttl", format="turtle"))

                # Apply the same pre-processing to our new dataframe
                new_features = new_df.drop(['index', 'CLASS'], axis=1)
                new_labels = new_df['CLASS']
                new_features, new_labels = preprocess(new_features, new_labels)
                new_features = new_features.reindex_axis(X_train.columns, axis=1)

                # Append it
                X_train = pd.concat([X_train, new_features])
                y_train = pd.concat([y_train, new_labels])

                new_df = decoder.decode(Graph().parse("SemanticProcessor/data/generated_samples_tension.ttl", format="turtle"))

                # Apply the same pre-processing to our new dataframe
                new_features = new_df.drop(['index', 'CLASS'], axis=1)
                new_labels = new_df['CLASS']
                new_features, new_labels = preprocess(new_features, new_labels)
                new_features = new_features.reindex_axis(X_train.columns, axis=1)

                # Append it
                X_train = pd.concat([X_train, new_features])
                y_train = pd.concat([y_train, new_labels])

            # Create decision tree model and fit it
            if weights is None:
                dt = tree.DecisionTreeClassifier(random_state=SEED, criterion='entropy')
            else:
                dt = tree.DecisionTreeClassifier(random_state=SEED, criterion='entropy', class_weight=weights)
            dt.fit(X_train, y_train)

            migbase_accs.append(accuracy_score(y_test, dt.predict(X_test)))
            migbase_f1.append(f1_score(y_test, dt.predict(X_test), average='micro'))

            chron_accs.append(accuracy_score(chronicals_labels, dt.predict(chronicals_features)))
            chron_f1.append(f1_score(chronicals_labels, dt.predict(chronicals_features), average='micro'))

            cluster_f1s.append(f1_score(y_test, dt.predict(X_test), labels=[0], average='micro'))
            tension_f1s.append(f1_score(y_test, dt.predict(X_test), labels=[1], average='micro'))
            migraine_f1s.append(f1_score(y_test, dt.predict(X_test), labels=[2], average='micro'))

            migbase_cohens.append(cohen_kappa_score(y_test, dt.predict(X_test)))

        print('Sampler = {:>15}: F1_Cluster = {}, F1_Tension = {}, F1_Migraine = {}'.format(sampler, np.mean(cluster_f1s), 
                                                                                       np.mean(tension_f1s), np.mean(migraine_f1s)))
        print('Sampler = {:>15}: Migbase test accuracy = {}; F1 = {} || Chronicals accuracy = {}; F1 = {}'.format(sampler, np.mean(migbase_accs),
                                                                                                              np.mean(migbase_f1), np.mean(chron_accs),
                                                                                                              np.mean(chron_f1)))
        if sampler not in f1_per_sampler:
            f1_per_sampler[sampler] = [np.mean(migbase_f1)]
            cluster_f1_per_sampler[sampler] = [np.mean(cluster_f1s)]
            tension_f1_per_sampler[sampler] = [np.mean(tension_f1s)]
            migraine_f1_per_sampler[sampler] = [np.mean(migraine_f1s)]
        else:
            f1_per_sampler[sampler].append(np.mean(migbase_f1))
            cluster_f1_per_sampler[sampler].append(np.mean(cluster_f1s))
            tension_f1_per_sampler[sampler].append(np.mean(tension_f1s))
            migraine_f1_per_sampler[sampler].append(np.mean(migraine_f1s))

        if sampler not in acc_per_sampler:
            acc_per_sampler[sampler] = [np.mean(migbase_accs)]
            cohen_per_sampler[sampler] = [np.mean(migbase_cohens)]
        else:
            acc_per_sampler[sampler].append(np.mean(migbase_accs))
            cohen_per_sampler[sampler].append(np.mean(migbase_cohens))

pickle.dump(f1_per_sampler, open('f1_per_sampler.p', 'wb'))
pickle.dump(acc_per_sampler, open('acc_per_sampler.p', 'wb'))
pickle.dump(cluster_f1_per_sampler, open('cluster_f1_per_sampler.p', 'wb'))
pickle.dump(tension_f1_per_sampler, open('tension_f1_per_sampler.p', 'wb'))
pickle.dump(migraine_f1_per_sampler, open('migraine_f1_per_sampler.p', 'wb'))
pickle.dump(cohen_per_sampler, open('kappa_cohen_per_sampler.p', 'wb'))
"""